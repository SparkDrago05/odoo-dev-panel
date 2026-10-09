import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from odoo_dev_panel import dbquery, logs
from odoo_dev_panel.dbintel import activity, meta
from odoo_dev_panel.run import RunSpec, build_argv

from .test_run import INST, instance


class FakeDb:
    """Answers json_rows by the first matching fragment of the SQL; records every statement."""

    def __init__(self, answers):
        self.answers, self.sql = answers, []

    async def __call__(self, root, database, sql, timeout_ms=15000):
        self.sql.append(sql)
        for fragment, rows in self.answers:
            if fragment in sql:
                if isinstance(rows, Exception):
                    raise rows
                return rows
        return []


def run(coro):
    return asyncio.run(coro)


class MetaTest(unittest.TestCase):
    def test_validation(self):
        for bad in ("x'; drop", "50%", "a" * 61):
            with self.assertRaises(meta.MetaError):
                run(meta.models("/r", "d", bad))
        with self.assertRaises(meta.MetaError):
            run(meta.models("/r", "d", None, 500))
        with self.assertRaises(meta.MetaError):
            run(meta.model("/r", "d", "Res Partner"))
        with self.assertRaises(meta.MetaError):
            run(meta.count("/r", "d", 'x"; drop'))

    def test_label(self):
        self.assertEqual(meta.label('{"en_US": "Contact", "fr_FR": "Contact FR"}'), "Contact")
        self.assertEqual(meta.label('{"fr_FR": "Seul"}'), "Seul")
        self.assertEqual(meta.label("Contact"), "Contact")
        self.assertIsNone(meta.label(None))

    def test_models_and_model(self):
        db = FakeDb([("FROM ir_model m", [{"model": "res.partner", "name": '{"en_US": "Contact"}', "transient": False,
                                           "state": "base", "modules": "base,mail", "fields": 200, "total": 7}]),
                     ("f.relation = 'res.partner'", [{"model": "sale.order", "name": "partner_id", "ttype": "many2one"}]),
                     ("WHERE f.model = 'res.partner'", [{"name": "parent_id", "ttype": "many2one", "relation": "res.partner",
                                                         "relation_field": None, "relation_table": None, "required": False,
                                                         "readonly": False, "store": True, "state": "base", "related": None,
                                                         "label": "Parent", "modules": "base"}])])
        with mock.patch.object(dbquery, "json_rows", db):
            out = run(meta.models("/r", "d", "res.part", 10, 20))
            self.assertEqual(out["models"][0]["name"], "Contact")
            self.assertEqual(out["models"][0]["modules"], ["base", "mail"])
            self.assertEqual(out["models"][0]["table"], "res_partner")
            self.assertEqual((out["total"], out["source"]), (7, "odoo"))
            self.assertIn("ILIKE '%res.part%'", db.sql[0])
            self.assertIn("LIMIT 10 OFFSET 20", db.sql[0])
            m = run(meta.model("/r", "d", "res.partner"))
            self.assertEqual(m["outgoing"], [{"field": "parent_id", "ttype": "many2one", "model": "res.partner"}])
            self.assertEqual(m["incoming"][0]["model"], "sale.order")
        with mock.patch.object(dbquery, "json_rows", FakeDb([])):
            with self.assertRaisesRegex(meta.MetaError, "no model"):
                run(meta.model("/r", "d", "nope.model"))

    def test_count_capped_and_missing_table(self):
        db = FakeDb([("c.relname = 'res_partner'", [{"relname": "res_partner"}]), ("count(*) AS n", [{"n": 101}])])
        with mock.patch.object(dbquery, "json_rows", db):
            out = run(meta.count("/r", "d", "res_partner", cap=100))
        self.assertEqual((out["count"], out["capped"]), (100, True))
        self.assertIn('FROM "res_partner" LIMIT 101', db.sql[-1])
        with mock.patch.object(dbquery, "json_rows", FakeDb([])):
            with self.assertRaisesRegex(meta.MetaError, "no table"):
                run(meta.count("/r", "d", "res_partner"))

    def test_query_error_becomes_meta_error(self):
        with mock.patch.object(dbquery, "json_rows", FakeDb([("FROM pg_class", dbquery.QueryError("timeout"))])):
            with self.assertRaisesRegex(meta.MetaError, "timeout"):
                run(meta.sizes("/r", "d"))


def session(**kw):
    base = {"pid": 1, "database": "d", "role": "odoo19", "state": "idle", "xact_s": None, "query_s": 1, "query": "SELECT 1",
            "blocked_by": [], "mine": True, "hidden": False}
    return {**base, **kw}


class VerdictTest(unittest.TestCase):
    SNAP = {"instances": [{"path": "/c.conf", "options": {"workers": "4", "db_maxconn": "64", "limit_memory_soft": "1000000",
                                                          "log_level": "info"}}]}

    def pg(self, sessions, connections=5, monitor=True):
        return {"server": {"max_connections": 100, "connections": connections, "monitor": monitor, "role": "odoo19"},
                "sessions": sessions, "locks": []}

    def test_calm(self):
        out = activity.verdict(self.pg([session()]), [], {"instances": []})
        self.assertEqual(out[0]["id"], "calm")

    def test_findings(self):
        sessions = [session(pid=2, state="active", query_s=30), session(pid=3, blocked_by=[2]),
                    session(pid=4, state="idle in transaction", xact_s=120),
                    session(pid=5, mine=False, hidden=True, query=None)]
        procs = [{"pid": 10, "instance": "a", "config": "/c.conf", "cpu_percent": 95.0, "rss_bytes": 4 * 1100000,
                  "processes": 4, "argv": ["odoo-bin", "--dev=xml"]}]
        out = activity.verdict(self.pg(sessions, connections=97, monitor=False), procs, self.SNAP)
        ids = [f["id"] for f in out]
        self.assertEqual(out[0]["id"], "connections")  # fail first
        for want in ("cpu-10", "mem-10", "blocked", "idle-xact", "long-queries", "maxconn-10", "dev-10", "visibility"):
            self.assertIn(want, ids)
        self.assertNotIn("calm", ids)
        self.assertIn("blocked by pid 2", next(f for f in out if f["id"] == "blocked")["detail"])

    def test_pg_error(self):
        out = activity.verdict(None, [], {"instances": []}, "no role")
        self.assertEqual(out[0]["id"], "pg-error")


class ProcessesTest(unittest.TestCase):
    def test_tree_cpu_and_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            def write(pid, ticks, rss):
                d = Path(tmp) / str(pid)
                d.mkdir(exist_ok=True)
                fields = ["S", "1"] + ["0"] * 9 + [str(ticks), "0"]
                (d / "stat").write_text(f"{pid} (python3) " + " ".join(fields))
                (d / "statm").write_text(f"1000 {rss} 0 0 0 0 0")

            write(10, 0, 100)
            write(11, 0, 50)

            def sleep(_s):
                write(10, os.sysconf("SC_CLK_TCK") // 2, 100)
                write(11, os.sysconf("SC_CLK_TCK") // 2, 50)

            snap = {"processes": [{"pid": 10, "ppid": 1, "config": "/c.conf", "argv": ["odoo-bin"]},
                                  {"pid": 11, "ppid": 10, "config": "/c.conf", "argv": ["odoo-bin"]}]}
            out = activity.processes(snap, 1.0, tmp, sleep)
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["pid"], out[0]["processes"]), (10, 2))
        self.assertEqual(out[0]["cpu_percent"], 100.0)
        self.assertEqual(out[0]["rss_bytes"], 150 * os.sysconf("SC_PAGE_SIZE"))


class CancelGrantTest(unittest.TestCase):
    def test_cancel(self):
        with self.assertRaises(activity.ActivityError):
            run(activity.cancel("/r", "d", "1; drop"))
        db = FakeDb([("pg_cancel_backend", [{"cancelled": True}])])
        with mock.patch.object(dbquery, "json_rows", db):
            self.assertEqual(run(activity.cancel("/r", "d", 42)), {"pid": 42, "cancelled": True})
        self.assertIn("a.usename = current_user", db.sql[0])
        self.assertNotIn("terminate", db.sql[0])
        with mock.patch.object(dbquery, "json_rows", FakeDb([])):
            with self.assertRaisesRegex(activity.ActivityError, "not a running query"):
                run(activity.cancel("/r", "d", 42))

    def test_hidden_rows_are_kept(self):
        # other roles' rows have backend_type NULL without pg_monitor
        self.assertIn("backend_type IS NULL AND a.usename IS NOT NULL", activity.SESSIONS_SQL)
        self.assertIn("backend_type IS NULL AND a.usename IS NOT NULL", activity.SERVER_SQL)

    def test_grant_script(self):
        self.assertIn('GRANT pg_monitor TO "odoo19"', activity.grant_script("odoo19"))
        self.assertIn('REVOKE pg_monitor FROM "odoo19"', activity.grant_script("odoo19", revoke=True))
        with self.assertRaises(activity.ActivityError):
            activity.grant_script('x"; DROP ROLE y; --')


SQL_LOG = """\
2026-10-09 10:00:00,001 1 DEBUG d odoo.sql_db: [1.500 ms] query: SELECT id FROM res_partner WHERE id IN (1, 2, 3) - - -
2026-10-09 10:00:00,002 1 DEBUG d odoo.sql_db: [3.000 ms] query: SELECT id FROM res_partner WHERE id IN (4, 5)
2026-10-09 10:00:00,003 1 DEBUG d odoo.sql_db: [0.250 ms] query: SELECT name
FROM res_users WHERE login = 'admin'
2026-10-09 10:00:00,004 1 INFO d odoo.modules: not sql
"""


class SqlLogTest(unittest.TestCase):
    def test_summary(self):
        out = logs.sql_summary(SQL_LOG)
        self.assertEqual((out["queries"], out["statements"], out["timed"]), (3, 2, True))
        top = out["top"][0]
        self.assertEqual(top["statement"], "SELECT id FROM res_partner WHERE id IN (?, ...)")
        self.assertEqual((top["count"], top["total_ms"], top["max_ms"]), (2, 4.5, 3.0))
        self.assertIn("login = ?", out["top"][1]["statement"])

    def test_untimed_sorted_by_count(self):
        text = "".join(f"2026-10-09 10:00:00,00{n} 1 DEBUG d odoo.sql_db: query: SELECT {n}\n" for n in range(3))
        text += "2026-10-09 10:00:01,000 1 DEBUG d odoo.sql_db: query: UPDATE x SET a = 1\n"
        out = logs.sql_summary(text)
        self.assertFalse(out["timed"])
        self.assertEqual(out["top"][0]["count"], 3)

    def test_log_sql_flag(self):
        argv = build_argv(INST, instance(), RunSpec.from_params({"log_sql": True}))
        self.assertIn("--log-sql", argv)


if __name__ == "__main__":
    unittest.main()
