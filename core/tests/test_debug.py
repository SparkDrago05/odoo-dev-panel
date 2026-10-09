import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from odoo_dev_panel.debug import launch, presets, vscode
from odoo_dev_panel.git import opener
from odoo_dev_panel.rpc import RpcError
from odoo_dev_panel.run import RunSpec, build_argv

from .helpers import core_env
from .test_run import INST, instance

SNAP = {
    "installations": [{"root": "/opt/odoo19", "source": "/opt/odoo19/odoo", "version": "19.0", "owner": "odoo19",
                       "venv": "/opt/odoo19/venv", "venv_python": "/opt/odoo19/venv/bin/python", "python_version": "3.12"}],
    "instances": [{"path": "/etc/odoo/odoo19/a.conf", "name": "a", "installation": "/opt/odoo19", "link": "addons_path",
                   "options": {"http_port": "8069"}}],
    "processes": [],
}
HAS_DEBUGPY = lambda inst: {"debugpy": "1.8.0"}  # noqa: E731
NO_DEBUGPY = lambda inst: {}  # noqa: E731


def preset(**kw):
    return presets.check({"id": "p1", "name": "Client A", "instance": "/etc/odoo/odoo19/a.conf", "database": "a_dev",
                          "port": 5678, **kw})


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {"ODP_CONFIG_DIR": str(self.tmp / "cfg")})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()


class RunDebugTest(unittest.TestCase):
    def test_debug_argv(self):
        spec = RunSpec.from_params({"db": "d", "debug_port": 5678, "debug_wait": True})
        argv = build_argv(INST, instance(), spec, port=8070)
        self.assertEqual(argv[:7], ["/opt/odoo19/venv/bin/python", "-m", "debugpy", "--listen", "127.0.0.1:5678",
                                    "--wait-for-client", "/opt/odoo19/odoo/odoo-bin"])
        self.assertEqual(argv[-2:], ["--workers=0", "--max-cron-threads=0"])
        plain = build_argv(INST, instance(), RunSpec.from_params({"db": "d"}))
        self.assertNotIn("--workers=0", plain)

    def test_debug_validation(self):
        for bad in ({"debug_port": 80}, {"debug_port": "5678"}, {"debug_wait": True},
                    {"db": "d", "shell": True, "debug_port": 5678}):
            with self.assertRaises(RpcError, msg=bad):
                RunSpec.from_params(bad)


class PresetTest(Fixture):
    def test_check(self):
        p = preset(dev="xml,qweb", update="sale")
        self.assertEqual(p["dev"], ["xml", "qweb"])
        self.assertEqual(p["kind"], "server")
        t = preset(kind="test", modules="sale_x", database="ignored", tags="/sale_x")
        self.assertIsNone(t["database"])
        for bad, msg in (({"id": "Bad Id"}, "id"), ({"port": None}, "port is required"), ({"port": 80}, "1024"),
                         ({"kind": "test"}, "needs modules"), ({"database": None, "update": "sale"}, "need a database"),
                         ({"dev": "nope"}, "--dev"), ({"bogus": 1}, "unknown field"), ({"http_port": 5678}, "differ"),
                         ({"instance": "rel.conf"}, "absolute")):
            with self.assertRaisesRegex(presets.PresetError, msg):
                preset(**bad)

    def test_save_ports_delete(self):
        self.assertEqual(presets.next_port(set()), 5678)
        presets.save(preset())
        with self.assertRaisesRegex(presets.PresetError, "exists"):
            presets.save(preset())
        with self.assertRaisesRegex(presets.PresetError, "used by preset p1"):
            presets.save({**preset(), "id": "p2"})
        self.assertEqual(presets.next_port({5679}), 5680)
        presets.save({**preset(), "name": "Renamed"}, overwrite=True)
        self.assertEqual(presets.get("p1")["name"], "Renamed")
        self.assertEqual(oct(presets.presets_file().stat().st_mode & 0o777), "0o600")
        self.assertTrue(presets.delete("p1"))
        self.assertEqual(presets.load(), [])
        self.assertTrue(list(presets.presets_file().parent.glob("debug-presets.json.bak-*")))
        self.assertFalse(presets.delete("p1"))
        with self.assertRaises(LookupError):
            presets.get("p1")


class LaunchTest(unittest.TestCase):
    def test_server_plan(self):
        p = asyncio.run(launch.plan(preset(dev="reload"), SNAP, {8069}, HAS_DEBUGPY))
        self.assertTrue(p["ok"])
        self.assertEqual(p["user"], "odoo19")
        self.assertIn("-m debugpy --listen 127.0.0.1:5678", p["commands"][0])
        self.assertEqual(p["session"]["meta"]["debug"], {"host": "127.0.0.1", "port": 5678, "wait": False})
        self.assertEqual(p["session"]["meta"]["preset"], "p1")
        self.assertNotEqual(p["session"]["meta"]["port"], 8069)  # busy: next free HTTP port
        ids = {c["id"]: c["status"] for c in p["checks"]}
        self.assertEqual(ids["local-users"], "warn")
        self.assertEqual(ids["reload"], "warn")

    def test_missing_debugpy_and_busy_port(self):
        p = asyncio.run(launch.plan(preset(), SNAP, {5678}, NO_DEBUGPY))
        self.assertFalse(p["ok"])
        failed = {c["id"] for c in p["checks"] if c["status"] == "fail"}
        self.assertEqual(failed, {"debugpy", "port"})
        with self.assertRaisesRegex(launch.LaunchError, "not a discovered"):
            asyncio.run(launch.plan(preset(instance="/etc/x.conf"), SNAP, set(), HAS_DEBUGPY))

    def test_start_server(self):
        planned = asyncio.run(launch.plan(preset(), SNAP, set(), HAS_DEBUGPY))
        conn = mock.AsyncMock()
        conn.request.return_value = {"id": "s1"}
        out = asyncio.run(launch.start(planned, conn, lambda e: None))
        method, sent = conn.request.call_args.args
        self.assertEqual(method, "session.start")
        self.assertNotIn("user", sent)
        self.assertEqual(out["attach"]["port"], 5678)
        planned["ok"] = False
        with self.assertRaises(launch.LaunchError):
            asyncio.run(launch.start(planned, conn, lambda e: None))


class VscodeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.file = os.path.join(self.root, ".vscode", "launch.json")

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, text):
        os.makedirs(os.path.dirname(self.file), exist_ok=True)
        Path(self.file).write_text(text)

    def test_new_file(self):
        p = vscode.plan(self.root, [preset()])
        self.assertTrue(p["ok"])
        self.assertEqual(p["added"], ["Odoo: Client A"])
        out = vscode.write(p)
        self.assertTrue(out["changed"])
        self.assertIsNone(out["backup"])
        data = json.loads(Path(self.file).read_text())
        self.assertEqual(data["configurations"][0]["connect"], {"host": "127.0.0.1", "port": 5678})
        self.assertEqual(data["configurations"][0]["type"], "debugpy")
        again = vscode.plan(self.root, [preset()])
        self.assertEqual(again["unchanged"], ["Odoo: Client A"])
        self.assertFalse(vscode.write(again)["changed"])

    def test_merge_keeps_others_and_conflict_needs_replace(self):
        other = {"name": "Mine", "type": "node", "request": "launch"}
        self.write(json.dumps({"version": "0.2.0", "configurations": [other, {**vscode.entry(preset()), "justMyCode": True}]}))
        p = vscode.plan(self.root, [preset()])
        self.assertFalse(p["ok"])
        self.assertEqual(p["conflicts"], ["Odoo: Client A"])
        p = vscode.plan(self.root, [preset()], replace=True)
        self.assertTrue(p["ok"])
        self.assertEqual((p["replaced"], p["kept"]), (["Odoo: Client A"], 1))
        self.assertIn('"justMyCode": false', p["diff"])
        out = vscode.write(p)
        self.assertTrue(Path(out["backup"]).exists())
        data = json.loads(Path(self.file).read_text())
        self.assertEqual(data["configurations"][0], other)

    def test_jsonc_and_invalid_are_not_rewritten(self):
        self.write('{\n  // mine\n  "version": "0.2.0",\n  "configurations": [],\n}\n')
        p = vscode.plan(self.root, [preset()])
        self.assertTrue(p["jsonc"])
        self.assertFalse(p["ok"])
        self.assertIn("Odoo: Client A", p["snippet"])
        with self.assertRaises(vscode.VscodeError):
            vscode.write(p)
        self.write("{nope")
        self.assertIn("not valid JSON", vscode.plan(self.root, [preset()])["checks"][0]["detail"])

    def test_changed_since_plan_and_no_presets(self):
        self.write(json.dumps({"version": "0.2.0", "configurations": []}))
        p = vscode.plan(self.root, [preset()])
        self.write(json.dumps({"version": "0.2.0", "configurations": [{"name": "x"}]}))
        with self.assertRaisesRegex(vscode.VscodeError, "changed since"):
            vscode.write(p)
        self.assertFalse(vscode.plan(self.root, [])["ok"])

    def test_jsonc_strip(self):
        self.assertEqual(json.loads(vscode._strip_jsonc('{"a": "x//y", /* c */ "b": [1,],}')), {"a": "x//y", "b": [1]})


class OpenFileTest(unittest.TestCase):
    def test_commands(self):
        def which(name):
            return f"/usr/bin/{name}" if name == ide else None

        for ide, want in (("code", ["/usr/bin/code", "-g", "/f.py:12"]),
                          ("pycharm", ["/usr/bin/pycharm", "--line", "12", "/f.py"]),
                          ("zed", ["/usr/bin/zed", "/f.py:12"])):
            with mock.patch.dict(os.environ, {"ODP_IDE": ide}):
                self.assertEqual(opener.file_command("/f.py", 12, which), want)
        with mock.patch.dict(os.environ, {"ODP_IDE": "code"}):
            ide = "code"
            self.assertEqual(opener.file_command("/f.py", None, which), ["/usr/bin/code", "/f.py"])

    def test_refusals(self):
        for file, line in (("rel.py", 1), ("/nope/x.py", 1), (__file__, 0), (__file__, True)):
            with self.assertRaises(opener.OpenError):
                opener.open_file(file, line)


class CliTest(Fixture):
    def test_add_list_edit_vscode(self):
        env = core_env(ODP_CONFIG_DIR=str(self.tmp / "cfg"), ODP_SOCKET_DIR=str(self.tmp))

        def odp(*args):
            return subprocess.run([sys.executable, "-m", "odoo_dev_panel", "debug", *args], capture_output=True,
                                  text=True, env=env, timeout=60)

        out = odp("add", "p1", "-c", "/etc/odoo/x.conf", "-d", "a_dev", "--port", "6001", "--dev", "xml")
        self.assertEqual(out.returncode, 0, out.stderr)
        out = odp("edit", "p1", "--wait", "--name", "Mine")
        self.assertEqual(out.returncode, 0, out.stderr)
        rows = json.loads(odp("list", "--json").stdout)
        self.assertEqual((rows[0]["name"], rows[0]["wait"], rows[0]["dev"], rows[0]["port"]), ("Mine", True, ["xml"], 6001))
        out = odp("add", "p2", "-c", "/etc/odoo/x.conf", "--kind", "test")
        self.assertEqual(out.returncode, 2)
        self.assertIn("needs modules", out.stderr)
        out = odp("plan", "nope")
        self.assertEqual(out.returncode, 2)


if __name__ == "__main__":
    unittest.main()


class SidecarTest(Fixture):
    def test_rpc(self):
        from .test_sidecar import frame, read_frame

        proc = subprocess.Popen([sys.executable, "-m", "odoo_dev_panel", "sidecar"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env=core_env(ODP_SOCKET_DIR=str(self.tmp), XDG_RUNTIME_DIR=str(self.tmp),
                                             ODP_CONFIG_DIR=str(self.tmp / "cfg")))
        try:
            def call(id_, method, params=None):
                proc.stdin.write(frame({"jsonrpc": "2.0", "id": id_, "method": method, "params": params}))
                proc.stdin.flush()
                while True:
                    msg = read_frame(proc.stdout)
                    if msg.get("id") == id_:
                        return msg

            port = call(1, "debug.next_port")["result"]["port"]
            self.assertGreaterEqual(port, 5678)
            saved = call(2, "debug.save", {"preset": {"id": "p1", "instance": "/etc/x.conf", "port": port}})["result"]
            self.assertEqual(saved["kind"], "server")
            self.assertEqual(call(3, "debug.presets")["result"]["presets"][0]["id"], "p1")
            self.assertIn("exists", call(4, "debug.save", {"preset": {"id": "p1", "instance": "/etc/x.conf", "port": port}})["error"]["message"])
            self.assertIn("not a discovered", call(5, "debug.plan", {"id": "p1"})["error"]["message"])
            self.assertEqual(call(6, "debug.plan", {"id": "nope"})["error"]["code"], -32001)
            self.assertIn("absolute", call(7, "debug.open", {"file": "x.py", "line": 1})["error"]["message"])
            self.assertTrue(call(8, "debug.delete", {"id": "p1"})["result"]["deleted"])
        finally:
            proc.stdin.close()
            proc.wait(10)
            proc.stdout.close()
