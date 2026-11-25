import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from odoo_dev_panel import compare, dbquery, modules
from odoo_dev_panel.database import commands as cmd
from tests.test_compare import make_install
from tests.test_modules import make


class Overlay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a = os.path.join(self.tmp.name, "a")
        make(self.a, "base", version="1.3")
        make(self.a, "sale", ["base"], version="19.0.1.2")
        make(self.a, "crm", ["sale"], version="1.0")
        self.full = modules.graph(modules.scan([self.a]))

    def test_full_version_adds_the_series(self):
        self.assertEqual(modules.full_version("1.2", "19.0"), "19.0.1.2")
        self.assertEqual(modules.full_version("19.0.1.2", "19.0"), "19.0.1.2")
        self.assertEqual(modules.full_version(None, "19.0"), None)
        self.assertEqual(modules.full_version("1.2", None), "1.2")

    def test_states_versions_and_db_only(self):
        states = {"base": {"state": "installed", "version": "19.0.1.3"},
                  "sale": {"state": "installed", "version": "18.0.1.2"},  # a database from an older series
                  "ghost": {"state": "installed", "version": "19.0.1.0"},
                  "gone": {"state": "uninstalled", "version": ""}}
        out = modules.overlay(self.full, states, "19.0")
        m = out["modules"]
        self.assertEqual((m["base"]["db_state"], m["base"]["version_differs"]), ("installed", False))
        self.assertTrue(m["sale"]["version_differs"])
        self.assertIsNone(m["crm"]["db_state"])
        self.assertFalse(m["crm"]["version_differs"])
        self.assertEqual(out["db_only"], ["ghost"])  # "gone" is uninstalled: not a problem
        self.assertEqual(out["db_counts"], {"installed": 2, "not in database": 1})

    def test_extra_folders_are_scanned_after_the_config(self):
        extra = os.path.join(self.tmp.name, "extra")
        make(extra, "mine", ["sale"])
        make(extra, "sale")  # hidden by the earlier folder
        snap = {"instances": [], "installations": []}
        conf = os.path.join(self.tmp.name, "odoo.conf")
        Path(conf).write_text(f"[options]\naddons_path = {self.a}\n")
        snap["instances"].append({"path": conf, "installation": None, "options": {"addons_path": self.a}})
        full = modules.for_config(conf, snap, extra=[extra, extra])
        self.assertEqual(full["addons_paths"], [self.a, extra])
        self.assertIn("mine", full["modules"])
        self.assertEqual([s["name"] for s in full["shadowed"]], ["sale"])
        self.assertEqual(full["extra"], [extra, extra])


class InstallationAndDatabaseCompare(unittest.TestCase):
    def test_installations(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            one = make_install(d / "o17", "17.0", {"lxml": "5.0", "x": "1"}, "a" * 40)
            two = make_install(d / "o18", "18.0", {"lxml": "5.1"}, "b" * 40)
            snap = {"installations": [one, two], "instances": []}
            r = compare.compare_installations(one["root"], two["root"], snap)
            facts = {f["key"]: f for f in r["facts"]}
            self.assertEqual((facts["version"]["a"], facts["version"]["b"]), ("17.0", "18.0"))
            self.assertFalse(facts["commit"]["same"])
            self.assertEqual(r["packages"]["changed"], {"lxml": ["5.0", "5.1"]})
            self.assertEqual(r["packages"]["only_a"], {"x": "1"})
            self.assertEqual(r["options"], {"only_a": {}, "only_b": {}, "changed": {}})
            self.assertEqual(r["addons"], {"only_a": [], "only_b": []})
            with self.assertRaises(KeyError):
                compare.compare_installations("/nope", two["root"], snap)

    def test_database_modules(self):
        a = {"base": {"state": "installed", "version": "19.0.1.3"}, "sale": {"state": "installed", "version": "19.0.1.2"},
             "crm": {"state": "uninstalled", "version": ""}, "mail": {"state": "to upgrade", "version": "19.0.1.0"}}
        b = {"base": {"state": "installed", "version": "19.0.1.3"}, "sale": {"state": "installed", "version": "18.0.1.2"},
             "hr": {"state": "installed", "version": "19.0.1.0"}, "crm": {"state": "uninstallable", "version": ""}}
        d = compare.diff_modules(a, b)
        self.assertEqual(d["only_a"], {"mail": "to upgrade 19.0.1.0"})
        self.assertEqual(d["only_b"], {"hr": "installed 19.0.1.0"})
        self.assertEqual(d["changed"], {"sale": ["installed 19.0.1.2", "installed 18.0.1.2"]})


class Query(unittest.TestCase):
    def test_local_rows_use_the_database_in_argv_only(self):
        seen = {}

        async def fake_local(argv, env):
            seen["argv"], seen["env"] = argv, env
            return ["base\tinstalled\t19.0.1.3", "sale\tuninstalled\t", ""]

        ctx = mock.Mock(conn=cmd.Conn("localhost", "5432", "odoo19", "s3cret"), agent_running=True, databases=[{"name": "shop"}])
        ctx.listing_error = None
        prepare = mock.AsyncMock(return_value=({"owner": "odoo19"}, ctx))
        with mock.patch.object(dbquery.context, "prepare", prepare), mock.patch.object(dbquery, "_local", fake_local):
            got = asyncio.run(dbquery.installed_modules("/opt/odoo19", "shop"))
            self.assertEqual(got, {"base": {"state": "installed", "version": "19.0.1.3"}, "sale": {"state": "uninstalled", "version": ""}})
            self.assertEqual(seen["argv"][seen["argv"].index("-d") + 1], "shop")
            self.assertNotIn("shop", dbquery.MODULE_SQL)
            self.assertNotIn("s3cret", " ".join(seen["argv"]))
            self.assertEqual(seen["env"], {"PGPASSWORD": "s3cret"})
            with self.assertRaises(dbquery.QueryError):
                asyncio.run(dbquery.installed_modules("/opt/odoo19", "other; DROP DATABASE shop"))

    def test_peer_authentication_needs_an_unlocked_agent(self):
        ctx = mock.Mock(conn=cmd.Conn(None, "5432", "someone_else"), agent_running=False, databases=[{"name": "shop"}])
        ctx.listing_error = None
        prepare = mock.AsyncMock(return_value=({"owner": "someone_else"}, ctx))
        with mock.patch.object(dbquery.context, "prepare", prepare):
            with self.assertRaises(dbquery.QueryError) as caught:
                asyncio.run(dbquery.installed_modules("/opt/odoo17", "shop"))
            self.assertIn("unlock the agent", str(caught.exception))

    def test_timeout_is_set_before_the_query(self):
        seen = {}

        async def fake_local(argv, env):
            seen["argv"] = argv
            return []

        ctx = mock.Mock(conn=cmd.Conn("localhost", "5432", "odoo19", "s3cret"), agent_running=True, databases=[{"name": "shop"}])
        prepare = mock.AsyncMock(return_value=({"owner": "odoo19"}, ctx))
        with mock.patch.object(dbquery.context, "prepare", prepare), mock.patch.object(dbquery, "_local", fake_local):
            asyncio.run(dbquery.rows("/opt/odoo19", "shop", "SELECT 1", timeout_ms=1500))
        argv = seen["argv"]
        self.assertLess(argv.index("SET statement_timeout = 1500"), argv.index("SELECT 1"))
        self.assertIn("-q", argv)

    def test_peer_authentication_reads_through_the_agent(self):
        ctx = mock.Mock(conn=cmd.Conn(None, "5432", "someone_else"), agent_running=True, databases=[{"name": "shop"}])
        prepare = mock.AsyncMock(return_value=({"owner": "someone_else"}, ctx))
        agent = mock.Mock(close=mock.AsyncMock())
        seen = {}

        async def fake_session(conn, step, argv, cwd, report):
            seen["argv"], seen["cwd"] = argv, cwd
            for text in ("base\tinstalled\t19.0.1.3", "sale\tinstalled\t19.0.1.0"):
                report({"status": "output", "text": text})
            report({"status": "done", "text": "ignored"})
            return 0, "s1"

        with mock.patch.object(dbquery.context, "prepare", prepare), \
                mock.patch.object(dbquery.client, "connect", mock.AsyncMock(return_value=agent)) as connect, \
                mock.patch.object(dbquery, "_agent_session", fake_session):
            got = asyncio.run(dbquery.installed_modules("/opt/odoo17", "shop"))
        connect.assert_awaited_once_with("someone_else")
        agent.close.assert_awaited_once()
        self.assertEqual(sorted(got), ["base", "sale"])
        self.assertEqual(seen["argv"][seen["argv"].index("-d") + 1], "shop")
        self.assertEqual(seen["cwd"], "/opt/odoo17")

    def test_agent_failure_reports_the_last_line(self):
        ctx = mock.Mock(conn=cmd.Conn(None, "5432", "someone_else"), agent_running=True, databases=[{"name": "shop"}])
        prepare = mock.AsyncMock(return_value=({"owner": "someone_else"}, ctx))
        agent = mock.Mock(close=mock.AsyncMock())

        async def fake_session(conn, step, argv, cwd, report):
            report({"status": "output", "text": 'ERROR:  relation "ir_module_module" does not exist'})
            return 1, "s1"

        with mock.patch.object(dbquery.context, "prepare", prepare), \
                mock.patch.object(dbquery.client, "connect", mock.AsyncMock(return_value=agent)), \
                mock.patch.object(dbquery, "_agent_session", fake_session):
            with self.assertRaises(dbquery.QueryError) as caught:
                asyncio.run(dbquery.installed_modules("/opt/odoo17", "shop"))
        self.assertIn("does not exist", str(caught.exception))
        agent.close.assert_awaited_once()

    def test_docker_rows(self):
        dctx = mock.Mock(list_error=None, db_container="shop-db-1", databases=[{"name": "shop"}])
        prepare = mock.AsyncMock(return_value=({}, dctx))
        sh = mock.AsyncMock(return_value=(0, ["base\tinstalled\t19.0.1.3"], []))
        from odoo_dev_panel import dockerdb

        with mock.patch.object(dockerdb, "prepare", prepare), mock.patch.object(dockerdb, "_sh", sh):
            got = asyncio.run(dbquery.installed_modules("docker:shop-web-1", "shop"))
            prepare.assert_awaited_once_with("shop-web-1")
            self.assertEqual(got, {"base": {"state": "installed", "version": "19.0.1.3"}})
            argv = sh.await_args.args[0]
            self.assertEqual(argv[:3], ["docker", "exec", "shop-db-1"])
            self.assertEqual(argv[argv.index("-d") + 1], "shop")
            with self.assertRaises(dbquery.QueryError) as caught:
                asyncio.run(dbquery.installed_modules("docker:shop-web-1", "other"))
            self.assertIn("is not a database in shop-db-1", str(caught.exception))
            sh.return_value = (2, [], ["psql: error: connection refused"])
            with self.assertRaises(dbquery.QueryError) as caught:
                asyncio.run(dbquery.installed_modules("docker:shop-web-1", "shop"))
            self.assertEqual(str(caught.exception), "psql: error: connection refused")

    def test_docker_listing_error(self):
        dctx = mock.Mock(list_error="the database container shop-db-1 is not running", databases=[])
        from odoo_dev_panel import dockerdb

        with mock.patch.object(dockerdb, "prepare", mock.AsyncMock(return_value=({}, dctx))):
            with self.assertRaises(dbquery.QueryError) as caught:
                asyncio.run(dbquery.installed_modules("docker:shop-web-1", "shop"))
        self.assertIn("is not running", str(caught.exception))

    def test_json_rows(self):
        # json_agg puts each row on its own line; a value with a tab or newline is escaped inside the JSON
        out = [['[{"query":"SELECT 1\\n\\tFROM x","calls":3}, '], ['{"query":"b","calls":1}]']]
        with mock.patch.object(dbquery, "rows", mock.AsyncMock(side_effect=[out, [[""]], [["oops"]]])) as rows:
            got = asyncio.run(dbquery.json_rows("/opt/odoo19", "shop", "SELECT query, calls FROM s"))
            self.assertEqual(got, [{"query": "SELECT 1\n\tFROM x", "calls": 3}, {"query": "b", "calls": 1}])
            sql, timeout = rows.await_args.args[2:]
            self.assertEqual(sql, "SELECT coalesce(json_agg(t), '[]'::json) FROM (SELECT query, calls FROM s) t")
            self.assertEqual(timeout, 15000)
            self.assertEqual(asyncio.run(dbquery.json_rows("/opt/odoo19", "shop", "SELECT 1")), [])
            with self.assertRaises(dbquery.QueryError):
                asyncio.run(dbquery.json_rows("/opt/odoo19", "shop", "SELECT 1"))

    def test_unknown_installation(self):
        async def missing(root):
            raise dbquery.context.NotFound(f"{root} is not a discovered Odoo installation")

        with mock.patch.object(dbquery.context, "prepare", missing):
            with self.assertRaises(dbquery.QueryError):
                asyncio.run(dbquery.installed_modules("/nope", "shop"))


if __name__ == "__main__":
    unittest.main()
