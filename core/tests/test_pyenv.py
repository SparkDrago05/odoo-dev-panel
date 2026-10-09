"""Y1-Y9: specifiers, environment description, install/validate plans and runs, dev tools, CLI/RPC parity."""

import asyncio
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from odoo_dev_panel import cli, rpc
from odoo_dev_panel.pyenv import actions, api, env, specs, tools
from odoo_dev_panel.sidecar import Sidecar

PY = f"{sys.version_info[0]}.{sys.version_info[1]}"


class SpecsTest(unittest.TestCase):
    def test_satisfies(self):
        for version, spec, want in (("5.2.1", "==5.2.1", True), ("5.2.1", ">=5,<6", True), ("6.0", "<6", False),
                                    ("2.0.3", "~=2.0", True), ("3.0", "~=2.0", False), ("1.4.2", "==1.4.*", True),
                                    ("1.5", "==1.4.*", False), ("2.0rc1", ">=2.0", False), ("1.0.post1", ">1.0", True),
                                    ("1.0", "!=1.0", False), ("x", "==1", None), ("1.0", "@@1", None)):
            self.assertEqual(specs.satisfies(version, spec), want, (version, spec))

    def test_requirement_spec(self):
        self.assertEqual(specs.requirement_spec('lxml==5.2.1 ; python_version > "3.10"'), "==5.2.1")
        self.assertEqual(specs.requirement_spec("Babel[x] >= 2.6, <3"), ">= 2.6, <3")
        self.assertEqual(specs.requirement_spec("requests"), "")

    def test_conflict(self):
        self.assertIn("different versions", specs.conflict(["==2.2.3", "==3.0"]))
        self.assertIn("excluded", specs.conflict(["==2.2.3", ">=3"]))
        self.assertIn("no version", specs.conflict([">=3", "<2"]))
        self.assertIsNone(specs.conflict([">=1", "<2"]))
        self.assertIsNone(specs.conflict(["==1.*", ">=1"]))
        self.assertIsNone(specs.conflict(["@@@", "==1"]))


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "opt" / "odoo19"
        src = self.root / "odoo"
        src.mkdir(parents=True)
        (src / "requirements.txt").write_text(
            "lxml==5.2.1\nBabel>=2.6\nmissingpkg\npsycopg2==2.9.9\n"
            'oldpy==1.0 ; python_version < "3.0"\nweird ; os_name =~ "x"\n')
        custom = self.root / "custom" / "hr"
        custom.mkdir(parents=True)
        (custom / "requirements.txt").write_text("lxml==4.9.0\npandas==2.2.3\n")
        venv = self.root / "venv"
        (venv / "bin").mkdir(parents=True)
        os.symlink(os.path.realpath(sys.executable), venv / "bin" / "python")
        (venv / "pyvenv.cfg").write_text(f"version_info = {PY}.0\n")
        site = venv / "lib" / f"python{PY}" / "site-packages"
        site.mkdir(parents=True)
        for dist in ("lxml-5.2.1", "babel-2.14.0", "psycopg2_binary-2.9.9", "pandas-3.0.6", "extra_tool-1.0"):
            (site / f"{dist}.dist-info").mkdir()
        self.inst = {"root": str(self.root), "source": str(src), "version": "19.0", "owner": "odoo19",
                     "venv": str(venv), "venv_python": str(venv / "bin" / "python")}
        self.snap = {"installations": [self.inst], "instances": [], "processes": [
            {"pid": 42, "installation": str(self.root), "user": "odoo19"}]}

    def tearDown(self):
        self._tmp.cleanup()


class DescribeTest(Fixture):
    def test_rows_conflicts_and_extras(self):
        d = env.describe(self.inst)
        by = {(r["name"], os.path.basename(os.path.dirname(r["file"]))): r for r in d["requirements"]}
        self.assertEqual(by[("lxml", "odoo")]["status"], "ok")
        self.assertEqual(by[("lxml", "hr")]["status"], "mismatch")
        self.assertEqual(by[("missingpkg", "odoo")]["status"], "missing")
        self.assertEqual(by[("psycopg2", "odoo")]["installed_as"], "psycopg2-binary")
        self.assertEqual(by[("oldpy", "odoo")]["status"], "not-applicable")
        self.assertEqual(by[("weird", "odoo")]["status"], "unknown")
        self.assertEqual(by[("pandas", "hr")]["status"], "mismatch")
        self.assertEqual([c["name"] for c in d["conflicts"]], ["lxml"])
        self.assertEqual(d["extras"], ["extra-tool"])
        self.assertEqual(d["interpreter"]["version"], PY)
        self.assertEqual(d["interpreter"]["pinned"], "3.12")

    def test_freeze_is_sanitized(self):
        text = env.freeze(self.inst)
        self.assertIn("lxml==5.2.1", text)
        self.assertNotIn(str(self.tmp), text)

    def test_unknown_installation(self):
        with self.assertRaises(env.EnvError):
            env.installation(self.snap, "/nope")


class PlanTest(Fixture):
    def test_install_plan(self):
        p = actions.install_plan(self.inst, self.snap["processes"], ["debugpy==1.8.1", "requests[socks]>=2"], missing=True,
                                 agent_running=True)
        self.assertIn("missingpkg", p["packages"])
        self.assertIn("lxml==4.9.0", p["packages"])  # mismatched requirement, with its specifier
        argv = p["session"]["argv"]
        self.assertEqual(argv[1:5], ["pip", "install", "--python", f"{self.inst['venv']}/bin/python"])
        self.assertEqual(p["session"]["user"], "odoo19")
        self.assertIn("CFLAGS", p["session"]["env"])
        self.assertEqual({c["id"]: c["status"] for c in p["checks"]}["running"], "warn")
        for bad in ("https://evil/x.tar.gz", "-r /etc/passwd", "pkg; rm -rf /", "../local", "--index-url=x"):
            with self.assertRaises(actions.ActionError, msg=bad):
                actions.install_plan(self.inst, [], [bad])
        with self.assertRaises(actions.ActionError):
            actions.install_plan(self.inst, [], [])

    def test_agent_and_venv_checks(self):
        p = actions.install_plan(self.inst, [], ["x"], agent_running=False)
        self.assertFalse(p["ok"])
        broken = dict(self.inst, venv=str(self.tmp / "novenv"))
        self.assertFalse(actions.install_plan(broken, [], ["x"])["ok"])

    def test_validate_plan_and_parse(self):
        p = actions.validate_plan(self.inst, [], agent_running=True)
        self.assertEqual(p["session"]["argv"][0], f"{self.inst['venv']}/bin/python")
        self.assertIn("psycopg2-binary", p["packages"])
        self.assertNotIn("missingpkg", p["packages"])
        out = 'noise\nODP-VALIDATE {"python": "3.12.3", "odoo": {"ok": true}, "modules": {"lxml": {"ok": false, "error": "x"}}}\n'
        self.assertFalse(actions.parse_validate(out)["modules"]["lxml"]["ok"])
        self.assertIsNone(actions.parse_validate("nothing"))

    def test_validate_script_runs(self):
        """The import check script itself, run by this interpreter against a fake odoo package."""
        fake = self.tmp / "src"
        (fake / "odoo").mkdir(parents=True)
        (fake / "odoo" / "__init__.py").write_text("class release:\n    version = '19.0'\n")
        import subprocess

        out = subprocess.run([sys.executable, "-c", actions.VALIDATE_SCRIPT, str(fake), "pip", "no-such-dist"],
                             capture_output=True, text=True, timeout=60)
        found = actions.parse_validate(out.stdout)
        self.assertTrue(found["odoo"]["ok"])
        self.assertEqual(found["odoo"]["version"], "19.0")
        self.assertTrue(found["modules"]["pip"]["ok"])
        self.assertEqual(found["modules"]["no-such-dist"]["error"], "not installed")

    def test_validate_script_picks_real_modules(self):
        """Seen on Odoo 17: libsass lists sasstests (needs pytest); PyPDF2 has no top_level.txt and its module is
        PyPDF2, not pypdf2."""
        import subprocess

        site = self.tmp / "site"
        site.mkdir()
        (site / "fakesass-1.0.dist-info").mkdir()
        (site / "fakesass-1.0.dist-info" / "METADATA").write_text("Metadata-Version: 2.1\nName: fakesass\nVersion: 1.0\n")
        (site / "fakesass-1.0.dist-info" / "top_level.txt").write_text("sass\nsasstests\n")
        (site / "sass.py").write_text("")
        (site / "sasstests.py").write_text("import pytest_not_here\n")
        (site / "FakePDF-2.0.dist-info").mkdir()
        (site / "FakePDF-2.0.dist-info" / "METADATA").write_text("Metadata-Version: 2.1\nName: FakePDF\nVersion: 2.0\n")
        (site / "FakePDF-2.0.dist-info" / "RECORD").write_text("FakePDF/__init__.py,,\nFakePDF-2.0.dist-info/METADATA,,\n")
        (site / "FakePDF").mkdir()
        (site / "FakePDF" / "__init__.py").write_text("")
        fake = self.tmp / "src"
        (fake / "odoo").mkdir(parents=True)
        (fake / "odoo" / "__init__.py").write_text("")
        out = subprocess.run([sys.executable, "-c", actions.VALIDATE_SCRIPT, str(fake), "fakesass", "fakepdf"],
                             capture_output=True, text=True, timeout=60, env={**os.environ, "PYTHONPATH": str(site)})
        found = actions.parse_validate(out.stdout)
        self.assertEqual(found["modules"]["fakesass"], {"ok": True, "error": None, "imports": ["sass"]})
        self.assertEqual(found["modules"]["fakepdf"]["imports"], ["FakePDF"])
        self.assertTrue(found["modules"]["fakepdf"]["ok"])


class FakeConn:
    def __init__(self, log, code=0):
        self.log, self.code, self.started = log, code, None

    async def request(self, method, params):
        if method == "session.start":
            self.started = params
            return {"id": "s1"}
        if method == "session.read":
            return {"offset": len(self.log), "data": self.log[params["offset"]:]}
        return {"state": "exited", "exit_code": self.code}


class RunTest(Fixture):
    def test_install_run_passes_env(self):
        p = actions.install_plan(self.inst, [], ["x==1"], agent_running=True)
        conn = FakeConn("Installed 1 package\n")
        out = asyncio.run(actions.run(p, conn, lambda e: None))
        self.assertTrue(out["ok"])
        self.assertIn("CFLAGS", conn.started["env"])

    def test_validate_run(self):
        p = actions.validate_plan(self.inst, [], agent_running=True)
        conn = FakeConn('ODP-VALIDATE {"python": "3", "odoo": {"ok": true}, "modules": {"lxml": {"ok": true}, "babel": {"ok": false, "error": "E"}}}\n')
        out = asyncio.run(actions.run(p, conn, lambda e: None))
        self.assertEqual((out["ok"], out["failed"]), (False, ["babel"]))


class ToolsTest(Fixture):
    def setUp(self):
        super().setUp()
        self._env = mock.patch.dict(os.environ, {"ODP_STATE_DIR": str(self.tmp / "state")})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        super().tearDown()

    def test_status(self):
        rows = {r["tool"]: r for r in tools.status(self.inst, which=lambda n: f"/usr/bin/{n}" if n != "rtlcss" else None,
                                                   run=lambda argv: "wkhtmltopdf 0.12.6 (with patched qt)")}
        self.assertFalse(rows["debugpy"]["installed"])
        self.assertFalse(rows["rtlcss"]["installed"])
        self.assertTrue(rows["wkhtmltopdf"]["patched"])
        rows = {r["tool"]: r for r in tools.status(None, which=lambda n: "/usr/bin/" + n, run=lambda argv: "wkhtmltopdf 0.12.6")}
        self.assertNotIn("debugpy", rows)
        self.assertIn("not the patched-Qt", rows["wkhtmltopdf"]["detail"])

    def test_plans(self):
        p = tools.plan("rtlcss")
        self.assertIn("npm install -g rtlcss", p["script"])
        p = tools.plan("wkhtmltopdf")
        self.assertIn(tools.WKHTML["sha256"], p["script"])
        self.assertIn("sha256sum -c", p["script"])
        p = tools.plan("debugpy", self.inst, [], True)
        self.assertEqual(p["packages"], ["debugpy"])
        with self.assertRaises(tools.ToolError):
            tools.plan("debugpy")
        with self.assertRaises(tools.ToolError):
            tools.plan("curl")

    def test_download_checks_hash(self):
        good = b"x" * 10

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        pinned = dict(tools.WKHTML, size=len(good), sha256=hashlib.sha256(good).hexdigest())
        with mock.patch.dict(tools.WKHTML, pinned):
            path = asyncio.run(tools.download(lambda e: None, opener=lambda url, timeout: Resp(good)))
            self.assertEqual(Path(path).read_bytes(), good)
        os.unlink(path)
        bad = dict(tools.WKHTML, size=len(good), sha256="0" * 64)
        with mock.patch.dict(tools.WKHTML, bad):
            with self.assertRaises(tools.ToolError):
                asyncio.run(tools.download(lambda e: None, opener=lambda url, timeout: Resp(good)))
        self.assertEqual([f for f in os.listdir(tools.downloads_dir()) if not f.startswith(".")], [])
        self.assertEqual(os.listdir(tools.downloads_dir()), [])

    def test_run_system_uses_root_runner(self):
        scripts = []

        async def root_runner(script, report):
            scripts.append(Path(script).read_text())
            return 0
        with mock.patch.object(tools, "status", return_value=[{"tool": "rtlcss", "installed": True}]):
            out = asyncio.run(tools.run_system(tools.plan("rtlcss"), lambda e: None, root_runner))
        self.assertTrue(out["ok"])
        self.assertIn("npm install -g rtlcss", scripts[0])


class ParityTest(Fixture):
    def test_show_cli_and_rpc(self):
        side = Sidecar()

        async def snap():
            return self.snap
        side._snap_procs = snap
        with mock.patch.object(api, "repos", return_value=[]):
            rpc_out = asyncio.run(side.h_python_env({"root": str(self.root)}, None))
            buf = io.StringIO()
            with mock.patch("odoo_dev_panel.discover.scan.scan", return_value=self.snap), contextlib.redirect_stdout(buf):
                cli.main(["python", "show", str(self.root), "--json"])
        self.assertEqual(json.loads(buf.getvalue()), rpc_out)

    def test_rpc_errors(self):
        side = Sidecar()

        async def snap():
            return self.snap
        side._snap_procs = snap
        with self.assertRaises(rpc.RpcError) as err:
            asyncio.run(side.h_python_env({"root": "/nope"}, None))
        self.assertEqual(err.exception.code, rpc.INVALID_PARAMS)
        with mock.patch.object(api, "repos", return_value=[]), \
                mock.patch("odoo_dev_panel.client.agent_status", new=mock.AsyncMock(return_value={"state": "stopped"})):
            with self.assertRaises(rpc.RpcError) as err:
                asyncio.run(side.h_python_run({"op": "install", "root": str(self.root), "packages": ["x"]}, None))
            self.assertEqual(err.exception.code, rpc.CONFLICT)
            with self.assertRaises(rpc.RpcError) as err:
                asyncio.run(side.h_python_plan({"op": "install", "root": str(self.root), "packages": ["http://x"]}, None))
            self.assertEqual(err.exception.code, rpc.INVALID_PARAMS)


if __name__ == "__main__":
    unittest.main()
