"""M1-M9: module center on a disposable installation: inventory, changed modules, manifest checks, upgrade and
test plans, a test run through a fake agent, scaffold, CLI/RPC parity."""

import asyncio
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from odoo_dev_panel import cli, rpc
from odoo_dev_panel.git import runner
from odoo_dev_panel.module_center import actions, center, changes, checks
from odoo_dev_panel.sidecar import Sidecar


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=runner.env())


def manifest(path: Path, **fields):
    path.mkdir(parents=True, exist_ok=True)
    data = {"name": path.name, "version": "19.0.1.0.0", "license": "LGPL-3", "depends": ["base"], **fields}
    (path / "__manifest__.py").write_text(repr(data))
    (path / "__init__.py").write_text("")


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        gitconfig = self.tmp / "gitconfig"
        gitconfig.write_text("[user]\n\tname = t\n\temail = t@example.com\n[init]\n\tdefaultBranch = main\n")
        self._env = mock.patch.dict(os.environ, {
            "GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1",
            "ODP_REPOSITORIES": str(self.tmp / "state" / "repositories.json"), "ODP_STATE_DIR": str(self.tmp / "state")})
        self._env.start()
        self.root = self.tmp / "opt" / "odoo19"
        src = self.root / "odoo"
        (src / "odoo").mkdir(parents=True)
        (src / "odoo-bin").write_text("")
        (src / "odoo" / "release.py").write_text("version_info = (19, 0)\n")
        manifest(src / "odoo" / "addons" / "base", depends=[])
        self.custom = self.root / "custom" / "edu"
        self.custom.mkdir(parents=True)
        git(self.custom, "init", "-q")
        manifest(self.custom / "adm", data=["views/v.xml"])
        (self.custom / "adm" / "views").mkdir()
        (self.custom / "adm" / "views" / "v.xml").write_text("<odoo/>")
        (self.custom / "adm" / "models.py").write_text("x = 1\n")
        manifest(self.custom / "exam", depends=["adm"])
        manifest(self.custom / "report", depends=["base"])
        git(self.custom, "add", "-A")
        git(self.custom, "commit", "-qm", "init")
        self.other = self.root / "custom" / "notloaded"
        self.other.mkdir(parents=True)
        git(self.other, "init", "-q")
        manifest(self.other / "lonely")
        self.conf = self.tmp / "main.conf"
        self.conf.write_text(f"[options]\naddons_path = {src}/odoo/addons,{self.custom}\n")
        self.snap = {
            "installations": [{"root": str(self.root), "source": str(src), "version": "19.0", "owner": "odoo19",
                               "venv_python": "/opt/odoo19/venv/bin/python"}],
            "instances": [{"path": str(self.conf), "name": "main", "installation": str(self.root),
                           "options": {"addons_path": f"{src}/odoo/addons,{self.custom}"}, "problems": [], "link": "addons_path"}],
            "databases": [{"installation": str(self.root), "databases": [{"name": "edu"}], "error": None}],
            "processes": [],
        }

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def inventory(self, **kw):
        return center.inventory(str(self.conf), self.snap, **kw)


class InventoryTest(Fixture):
    def test_loadable_own_and_repo(self):
        g = self.inventory(with_changes=False)
        mods = g["modules"]
        self.assertTrue(mods["adm"]["loadable"] and mods["adm"]["own"])
        self.assertEqual(mods["adm"]["repo"], str(self.custom))
        self.assertFalse(mods["base"]["own"])
        self.assertFalse(mods["lonely"]["loadable"])
        self.assertIn("not-loaded", [p["code"] for p in mods["lonely"]["problems"]])
        self.assertEqual(mods["adm"]["problems"], [])

    def test_unknown_config(self):
        with self.assertRaises(center.CenterError):
            center.inventory("/nope.conf", self.snap)


class ChangesTest(Fixture):
    def test_kinds_guidance_and_dependents(self):
        (self.custom / "adm" / "models.py").write_text("x = 2\n")
        manifest(self.custom / "brandnew")
        (self.custom / "report" / "__manifest__.py").unlink()
        g = self.inventory()
        ch = g["changed"]["modules"]
        self.assertEqual(ch["adm"]["kinds"], ["python"])
        self.assertEqual(ch["adm"]["action"], "review")
        self.assertEqual(ch["exam"]["dependency_changed"], ["adm"])
        self.assertTrue(ch["brandnew"]["new"])
        self.assertEqual(ch["brandnew"]["action"], "install")
        self.assertTrue(ch["report"]["removed"])
        self.assertTrue(g["changed"]["heuristic"])

    def test_data_change_means_upgrade(self):
        (self.custom / "adm" / "views" / "v.xml").write_text("<odoo><data/></odoo>")
        ch = self.inventory()["changed"]["modules"]
        self.assertEqual((ch["adm"]["kinds"], ch["adm"]["action"]), (["data"], "upgrade"))

    def test_kind_of(self):
        for rel, kind in (("__manifest__.py", "manifest"), ("models/x.py", "python"), ("views/a.xml", "data"),
                          ("security/ir.model.access.csv", "data"), ("static/src/x.js", "assets"),
                          ("i18n/fr.po", "i18n"), ("tests/test_x.py", "tests"), ("README.md", "other")):
            self.assertEqual(changes.kind_of(rel), kind, rel)


class ChecksTest(Fixture):
    def test_problems(self):
        manifest(self.custom / "bad", version="1.x", license="MIT", depends=["ghost"], data=["missing.xml"])
        (self.custom / "bad" / "__init__.py").unlink()
        manifest(self.custom / "old", version="18.0.1.0.0")
        codes = {p["code"] for p in self.inventory(with_changes=False)["modules"]["bad"]["problems"]}
        self.assertTrue({"bad-version", "bad-license", "missing-depends", "missing-files", "no-init"} <= codes, codes)
        old = self.inventory(with_changes=False)["modules"]["old"]["problems"]
        self.assertEqual([p["code"] for p in old], ["series-version"])

    def test_cycle(self):
        manifest(self.custom / "p", depends=["q"])
        manifest(self.custom / "q", depends=["p"])
        probs = self.inventory(with_changes=False)["modules"]["p"]["problems"]
        self.assertIn("cycle", [x["code"] for x in probs])


class PlanTest(Fixture):
    def graph(self, states=None):
        g = self.inventory(with_changes=False)
        for name, st in (states or {}).items():
            g["modules"][name]["db_state"] = st
        return g

    def test_upgrade_plan(self):
        p = actions.upgrade_plan(str(self.conf), self.snap, self.graph({"adm": "installed"}), "edu", ["adm"])
        self.assertTrue(p["ok"], p["checks"])
        argv = p["session"]["argv"]
        self.assertEqual(argv[argv.index("-u") + 1], "adm")
        self.assertIn("--stop-after-init", argv)
        self.assertEqual([s["id"] for s in p["steps"]], ["snapshot", "upgrade"])
        p = actions.upgrade_plan(str(self.conf), self.snap, self.graph({"exam": "uninstalled"}), "edu", ["exam"])
        self.assertFalse(p["ok"])
        p = actions.upgrade_plan(str(self.conf), self.snap, self.graph(), "nodb", ["lonely"], snapshot_first=False)
        self.assertFalse(p["ok"])
        self.assertEqual([s["id"] for s in p["steps"]], ["upgrade"])
        with self.assertRaises(actions.ActionError):
            actions.upgrade_plan(str(self.conf), self.snap, self.graph(), "edu", ["Bad Name"])
        # -i into a new database: allowed (Odoo creates it), but there is nothing to snapshot
        p = actions.upgrade_plan(str(self.conf), self.snap, self.graph(), "newdb", ["exam"], install=True, snapshot_first=False)
        self.assertTrue(p["ok"], p["checks"])
        self.assertFalse(actions.upgrade_plan(str(self.conf), self.snap, self.graph(), "newdb", ["exam"], install=True)["ok"])

    def test_test_plan_flags(self):
        now = datetime(2026, 10, 9, 12, 0, 0)
        p = actions.test_plan(str(self.conf), self.snap, self.graph(), ["adm", "exam"], now=now)
        self.assertEqual(p["database"], "odp_test_adm_20261009_120000")
        argv = p["session"]["argv"]
        self.assertEqual(argv[argv.index("--test-tags") + 1], "/adm,/exam")
        self.assertIn("--with-demo", argv)
        self.assertEqual(p["session"]["meta"]["kind"], "test")
        self.assertEqual(actions.test_flags(17, ["a"], None, False)[-1], "--without-demo=all")
        self.assertNotIn("--with-demo", actions.test_flags(17, ["a"], None, True))
        self.assertNotIn("--with-demo", actions.test_flags(19, ["a"], None, False))
        with self.assertRaises(actions.ActionError):
            actions.test_plan(str(self.conf), self.snap, self.graph(), ["adm"], tags="x; rm -rf /")


class ResultsTest(unittest.TestCase):
    def test_parse_new_and_old_formats(self):
        new = ("2026-10-09 10:00:00,000 1 INFO t odoo.tests.result: 1 failed, 1 error(s) of 12 tests when loading database 't'\n"
               "2026-10-09 10:00:00,000 1 ERROR t odoo.addons.adm.tests.test_a: FAIL: test_x (odoo.addons.adm.tests.test_a.TestA.test_x)\n"
               "2026-10-09 10:00:00,000 1 ERROR t odoo.addons.adm.tests.test_a: ERROR: test_y (odoo.addons.adm.tests.test_a.TestA.test_y)\n")
        r = actions.parse_results(new, 0)
        self.assertEqual((r["status"], r["tests"], r["failures"], r["errors"]), ("failed", 12, 1, 1))
        self.assertEqual(r["failed"][0], {"kind": "fail", "test": "odoo.addons.adm.tests.test_a.TestA.test_x"})
        old = "INFO t odoo.modules.module: odoo.modules.module: Module adm: 0 failures, 0 errors of 5 tests\n"
        self.assertEqual(actions.parse_results(old, 0)["status"], "passed")
        self.assertEqual(actions.parse_results("nothing", 0)["status"], "no-tests")
        # real Odoo 17 output (container test, 2026-10-09)
        real = ("2026-10-09 07:04:25,484 7603 ERROR odp_test_x odoo.addons.odp_demo.tests.test_basic: FAIL: TestBasic.test_fails_on_purpose\n"
                "2026-10-09 07:04:25,514 7603 ERROR odp_test_x odoo.modules.loading: Module odp_demo: 1 failures, 0 errors of 2 tests \n"
                "2026-10-09 07:04:27,651 7603 ERROR odp_test_x odoo.tests.result: 1 failed, 0 error(s) of 2 tests when loading database 'odp_test_x'\n")
        r = actions.parse_results(real, 0)
        self.assertEqual((r["status"], r["tests"], r["failures"]), ("failed", 2, 1))
        self.assertEqual(r["failed"], [{"kind": "fail", "test": "odoo.addons.odp_demo.tests.test_basic.TestBasic.test_fails_on_purpose"}])
        self.assertEqual(actions.parse_results("0 failed, 0 error(s) of 3 tests", 1)["status"], "failed")


class FakeConn:
    def __init__(self, log, code):
        self.log, self.code, self.started = log, code, None

    async def request(self, method, params):
        if method == "session.start":
            self.started = params
            return {"id": "s1"}
        if method == "session.read":
            data = self.log[params["offset"]:]
            return {"offset": len(self.log), "data": data}
        if method == "session.get":
            return {"state": "exited", "exit_code": self.code}
        raise AssertionError(method)


class RunTest(Fixture):
    def plan(self):
        g = self.inventory(with_changes=False)
        return actions.test_plan(str(self.conf), self.snap, g, ["adm"])

    def test_passed_run_drops_and_records(self):
        conn = FakeConn("INFO odoo.tests.result: 0 failed, 0 error(s) of 3 tests\n", 0)
        with mock.patch.object(actions, "_drop", new=mock.AsyncMock()) as drop:
            entry = asyncio.run(actions.test_run(self.plan(), conn, lambda e: None))
        drop.assert_awaited_once()
        self.assertEqual((entry["status"], entry["kept"]), ("passed", False))
        self.assertEqual(conn.started["meta"]["kind"], "test")
        self.assertEqual(actions.history()[0]["id"], "s1")

    def test_failed_run_keeps_then_drop_kept(self):
        conn = FakeConn("FAIL: test_x (odoo.addons.adm.tests.T.test_x)\n1 failed, 0 error(s) of 3 tests\n", 0)
        with mock.patch.object(actions, "_drop", new=mock.AsyncMock()) as drop:
            entry = asyncio.run(actions.test_run(self.plan(), conn, lambda e: None))
            drop.assert_not_awaited()
            self.assertTrue(entry["kept"])
            again = asyncio.run(actions.drop_kept("s1", lambda e: None))
            drop.assert_awaited_once()
        self.assertFalse(again["kept"])
        with self.assertRaises(actions.ActionError):
            asyncio.run(actions.drop_kept("s1", lambda e: None))

    def test_drop_refuses_non_test_database(self):
        with self.assertRaisesRegex(actions.ActionError, "not a test database"):
            asyncio.run(actions._drop(str(self.root), "edu", lambda e: None))

    def test_upgrade_run_with_snapshot(self):
        g = self.inventory(with_changes=False)
        g["modules"]["adm"]["db_state"] = "installed"
        p = actions.upgrade_plan(str(self.conf), self.snap, g, "edu", ["adm"])
        conn = FakeConn("2026-10-09 10:00:00,000 1 INFO edu odoo.modules.loading: done\n", 0)
        with mock.patch.object(actions, "_snapshot", new=mock.AsyncMock(return_value="/b/edu-1")):
            out = asyncio.run(actions.upgrade_run(p, conn, lambda e: None))
        self.assertEqual((out["exit_code"], out["backup"]), (0, "/b/edu-1"))


class ScaffoldTest(Fixture):
    def test_create_and_refusals(self):
        p = actions.scaffold_plan(self.snap, str(self.root), str(self.custom), "attendance_x", depends=["base"])
        self.assertIn('"version": "19.0.1.0.0"', p["files"]["__manifest__.py"])
        created = actions.scaffold_create(p)
        self.assertEqual(len(created), 5)
        probs = checks.check_module("attendance_x", str(self.custom / "attendance_x"), "19.0", {"base"})
        self.assertEqual([x for x in probs if x["level"] == "error"], [])
        with self.assertRaisesRegex(actions.ActionError, "exists"):
            actions.scaffold_plan(self.snap, str(self.root), str(self.custom), "attendance_x")
        with self.assertRaisesRegex(actions.ActionError, "neither inside"):
            actions.scaffold_plan(self.snap, str(self.root), str(self.tmp), "x_mod")
        with self.assertRaises(actions.ActionError):
            actions.scaffold_plan(self.snap, str(self.root), str(self.custom), "1bad")


class ParityTest(Fixture):
    def test_changed_cli_and_rpc(self):
        (self.custom / "adm" / "models.py").write_text("x = 3\n")
        side = Sidecar()

        async def snap():
            return self.snap
        side._snap = snap
        rpc_out = asyncio.run(side.h_modules_center({"config": str(self.conf)}, None))["changed"]
        buf = io.StringIO()
        with mock.patch("odoo_dev_panel.discover.scan.scan", return_value=self.snap), contextlib.redirect_stdout(buf):
            code = cli.main(["modules", "changed", str(self.conf), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(buf.getvalue()), rpc_out)

    def test_rpc_errors(self):
        side = Sidecar()

        async def snap():
            return self.snap
        side._snap = snap
        with self.assertRaises(rpc.RpcError) as err:
            asyncio.run(side.h_modules_plan({"kind": "upgrade", "config": str(self.conf), "modules": ["adm"]}, None))
        self.assertEqual(err.exception.code, rpc.INVALID_PARAMS)
        with self.assertRaises(rpc.RpcError) as err:
            asyncio.run(side.h_modules_run({"kind": "upgrade", "config": str(self.conf), "modules": ["lonely"], "database": "edu"}, None))
        self.assertEqual(err.exception.code, rpc.CONFLICT)
        with self.assertRaises(rpc.RpcError):
            asyncio.run(side.h_modules_plan({"kind": "nuke", "config": str(self.conf)}, None))

    def test_graph_verb_and_old_form(self):
        for argv in (["modules", str(self.conf), "--json"], ["modules", "graph", str(self.conf), "--json"]):
            buf = io.StringIO()
            with mock.patch("odoo_dev_panel.discover.scan.scan", return_value=self.snap), contextlib.redirect_stdout(buf):
                self.assertEqual(cli.main(argv), 0)
            self.assertIn("adm", json.loads(buf.getvalue())["modules"])


if __name__ == "__main__":
    unittest.main()
