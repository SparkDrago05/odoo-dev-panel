import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from odoo_dev_panel.tasks import builtin, runner, steps, workflow

from .helpers import core_env
from .test_sidecar import frame, read_frame

SNAP = {
    "installations": [{"root": "/opt/odoo17", "owner": "odoo17", "version": "17.0"}],
    "instances": [{"path": "/opt/odoo17/conf/a.conf", "name": "a", "installation": "/opt/odoo17"},
                  {"path": "/etc/loose.conf", "name": "loose", "installation": None}],
    "processes": [],
}

COMMANDS = '''\
name = "Two commands"

[params.word]
kind = "text"
default = "hello"

[[steps]]
op = "command"
title = "Say {word}"
argv = ["echo", "{word}"]

[[steps]]
op = "command"
argv = ["sh", "-c", "test -e {word}"]
'''


async def _yes(step, plan):
    return True


class FakeEnv:
    user = "dev"

    def __init__(self):
        self.cancel = False

    async def snapshot(self, databases):
        return SNAP

    async def agent(self, user):
        raise AssertionError("no agent in these tests")

    def cancelled(self):
        return self.cancel


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {"ODP_CONFIG_DIR": str(self.tmp / "cfg"),
                                                 "ODP_STATE_DIR": str(self.tmp / "state")})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()


class WorkflowTest(Fixture):
    def test_builtins_parse(self):
        for name, text in builtin.RECIPES.items():
            data = workflow.parse(text, name)
            self.assertTrue(data["steps"], name)
        self.assertIn("installation", workflow.known_names(workflow.parse(builtin.RECIPES["pull-and-upgrade"], "x")))

    def test_shape_errors(self):
        def err(text):
            with self.assertRaises(workflow.WorkflowError) as cm:
                workflow.parse(text, "t")
            return str(cm.exception)

        self.assertIn("steps must be", err('name = "x"'))
        self.assertIn("op must be one of", err('[[steps]]\nop = "rm"'))
        self.assertIn("unknown key 'force'", err('[[steps]]\nop = "git.pull"\nforce = true'))
        self.assertIn("database is required", err('[[steps]]\nop = "db.snapshot"'))
        self.assertIn("unknown parameter {db}", err('[[steps]]\nop = "db.snapshot"\ndatabase = "{db}"'))
        self.assertIn("kind must be one of", err('[params.x]\nkind = "file"\n[[steps]]\nop = "git.pull"'))
        self.assertIn("as is", err('[[steps]]\nop = "command"\nargv = ["ls"]\nas = "root"'))
        self.assertIn("argv is a non-empty list", err('[[steps]]\nop = "command"\nargv = []'))
        self.assertIn("modules must be a list", err('[[steps]]\nop = "modules.test"\nconfig = "/c"\nmodules = "base"'))
        self.assertIn("default", err('[params.m]\nkind = "modules"\ndefault = "Bad Name"\n[[steps]]\nop = "git.pull"'))
        self.assertIn("auto has the wrong type", err('[[steps]]\nop = "git.pull"\nauto = "yes"'))

    def test_bind_and_fill(self):
        data = workflow.parse(builtin.RECIPES["safe-upgrade"], "x")
        values = workflow.bind(data, {"config": "/opt/odoo17/conf/a.conf", "database": "db1", "modules": "sale, stock"}, SNAP)
        self.assertEqual(values["modules"], ["sale", "stock"])
        self.assertEqual(values["installation"], "/opt/odoo17")
        self.assertEqual(workflow.fill("{modules}", values), ["sale", "stock"])
        self.assertEqual(workflow.fill("Upgrade {modules} in {database}", values), "Upgrade sale,stock in db1")
        self.assertEqual(workflow.fill(["-u", "{modules}", "x{database}"], values), ["-u", "sale", "stock", "xdb1"])
        with self.assertRaisesRegex(workflow.WorkflowError, "database is required"):
            workflow.bind(data, {"config": "/opt/odoo17/conf/a.conf", "modules": "sale"}, SNAP)
        with self.assertRaisesRegex(workflow.WorkflowError, "not a discovered Odoo config"):
            workflow.bind(data, {"config": "/x.conf", "database": "d", "modules": "sale"}, SNAP)
        with self.assertRaisesRegex(workflow.WorkflowError, "not linked"):
            workflow.bind(data, {"config": "/etc/loose.conf", "database": "d", "modules": "sale"}, SNAP)
        with self.assertRaisesRegex(workflow.WorkflowError, "bad database name"):
            workflow.bind(data, {"config": "/opt/odoo17/conf/a.conf", "database": "a;b", "modules": "sale"}, SNAP)
        with self.assertRaisesRegex(workflow.WorkflowError, "unknown parameter"):
            workflow.bind(data, {"nope": "1"}, SNAP)

    def test_save_list_load_delete(self):
        with self.assertRaisesRegex(workflow.WorkflowError, "built-in"):
            workflow.save("safe-upgrade", COMMANDS)
        with self.assertRaises(workflow.WorkflowError):
            workflow.save("bad", "[[steps]]\nop = 'nope'")
        path = workflow.save("mine", "# keep me\n" + COMMANDS)
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        with self.assertRaisesRegex(workflow.WorkflowError, "exists"):
            workflow.save("mine", COMMANDS)
        (workflow.workflows_dir() / "broken.toml").write_text("x = ")
        rows = {r["name"]: r for r in workflow.list_workflows()}
        self.assertEqual(rows["mine"]["source"], "saved")
        self.assertEqual(rows["mine"]["params"], ["word"])
        self.assertTrue(rows["broken"]["error"])
        self.assertEqual(rows["safe-upgrade"]["source"], "built-in")
        loaded = workflow.load("mine")
        self.assertTrue(loaded["text"].startswith("# keep me"))
        moved = workflow.delete("mine")
        self.assertIn(".trash-mine-", moved)
        self.assertTrue(Path(moved).exists())
        with self.assertRaises(LookupError):
            workflow.load("mine")

    def test_gates(self):
        self.assertTrue(steps.gated({"op": "modules.upgrade"}))
        self.assertFalse(steps.gated({"op": "modules.upgrade", "auto": True}))
        self.assertTrue(steps.gated({"op": "command", "argv": ["x"], "auto": True}))
        self.assertTrue(steps.gated({"op": "db.drop", "auto": True}))
        self.assertFalse(steps.gated({"op": "db.snapshot"}))
        self.assertTrue(steps.gated({"op": "db.snapshot", "confirm": True}))


class RunnerTest(Fixture):
    def setUp(self):
        super().setUp()
        self.events = []
        self.asked = []
        self.answer = True

    async def confirm(self, step, plan):
        self.asked.append((step, plan["commands"]))
        return self.answer

    def run_wf(self, name, params=None, start=0, retry_of=None, env=None):
        return asyncio.run(runner.run(name, params, env or FakeEnv(), self.events.append, self.confirm, start, retry_of))

    def test_preview_shows_identity_commands_and_gates(self):
        workflow.save("cmds", COMMANDS)
        shown = asyncio.run(runner.preview("cmds", {"word": "hi there"}, FakeEnv()))
        self.assertEqual(shown["gates"], 2)
        first = shown["steps"][0]
        self.assertEqual(first["title"], "Say hi there")
        self.assertEqual(first["commands"], ["echo 'hi there'"])
        self.assertIn("dev (you", first["identity"])
        self.assertTrue(first["ok"])
        self.assertNotIn("raw", first)

    def test_run_gate_fail_retry_and_history(self):
        workflow.save("cmds", COMMANDS)
        target = self.tmp / "marker"
        result = self.run_wf("cmds", {"word": str(target)})
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failed_at"], 1)
        self.assertEqual([s["status"] for s in result["steps"]], ["ok", "fail"])
        self.assertEqual(len(self.asked), 2)
        self.assertIn({"task_step": 0, "step": "command", "status": "output", "text": str(target)}, self.events)
        rows = runner.history()
        self.assertEqual(rows[0]["status"], "fail")
        self.assertEqual(rows[0]["steps"][0]["commands"], [f"echo {target}"])

        point = runner.retry_point(result["run_id"])
        self.assertEqual(point["start"], 1)
        target.write_text("")
        self.asked.clear()
        again = self.run_wf(point["workflow"], point["params"], point["start"], result["run_id"])
        self.assertEqual(again["status"], "ok")
        self.assertEqual([s["index"] for s in again["steps"]], [1])
        self.assertEqual(self.asked[0][0], 1)
        self.assertEqual(runner.history()[0]["retry_of"], result["run_id"])
        with self.assertRaisesRegex(runner.RunError, "finished"):
            runner.retry_point(again["run_id"])

    def test_declined_gate_stops_and_changed_workflow_refuses_retry(self):
        workflow.save("cmds", COMMANDS)
        self.answer = False
        result = self.run_wf("cmds")
        self.assertEqual(result["status"], "declined")
        self.assertEqual(result["failed_at"], 0)
        self.assertNotIn("output", [e["status"] for e in self.events])
        workflow.save("cmds", COMMANDS.replace("hello", "bye"), overwrite=True)
        with self.assertRaisesRegex(runner.RunError, "changed since"):
            runner.retry_point(result["run_id"])

    def test_failed_check_stops_before_running(self):
        workflow.save("nope", '[[steps]]\nop = "command"\nargv = ["odp-no-such-program-x"]\n'
                              '[[steps]]\nop = "command"\nargv = ["true"]\n')
        result = self.run_wf("nope")
        self.assertEqual(result["status"], "fail")
        self.assertIn("not on PATH", result["steps"][0]["summary"])
        self.assertEqual(self.asked, [])
        self.assertEqual(len(result["steps"]), 1)

    def test_cancel_and_interrupted_run(self):
        workflow.save("cmds", COMMANDS)
        env = FakeEnv()

        async def cancel_on_ask(step, plan):
            env.cancel = True
            return True

        result = asyncio.run(runner.run("cmds", None, env, self.events.append, cancel_on_ask))
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["failed_at"], 1)
        # a run whose process died has no end line: it can be retried unless it is the active one
        runner._append({"run": "dead", "type": "start", "at": "2099-01-01T00:00:00+00:00", "workflow": "cmds",
                        "digest": workflow.load("cmds")["digest"], "params": {"word": "x"}, "start": 0,
                        "steps": ["a", "b"]})
        runner._append({"run": "dead", "type": "step", "at": "2099-01-01T00:00:01+00:00", "index": 0, "status": "ok",
                        "title": "a"})
        self.assertEqual(runner.history()[0]["status"], "running")
        self.assertEqual(runner.retry_point("dead")["start"], 1)
        with self.assertRaisesRegex(runner.RunError, "not ended"):
            runner.retry_point("dead", active={"dead"})

    def test_history_trims(self):
        file = self.tmp / "h.jsonl"
        with mock.patch.object(runner, "HISTORY_MAX_BYTES", 2000):
            for n in range(100):
                runner._append({"run": f"r{n}", "type": "start", "at": f"2026-01-01T00:00:{n:02d}", "workflow": "w",
                                "steps": []}, file)
        self.assertLess(file.stat().st_size, 4000)
        self.assertEqual(runner.history(file)[0]["run"], "r99")


class StepPlanTest(Fixture):
    def test_command_as_run_as_needs_installation(self):
        env = FakeEnv()
        planned = asyncio.run(runner.plan_step({"op": "command", "argv": ["ls"], "as": "run-as"}, {}, env))
        self.assertFalse(planned["ok"])
        self.assertIn("installation", planned["checks"][0]["detail"])
        planned = asyncio.run(runner.plan_step({"op": "command", "argv": ["ls", "-l"], "as": "run-as",
                                                "installation": "/opt/odoo17"}, {}, env))
        self.assertTrue(planned["ok"])
        self.assertEqual(planned["raw"]["argv"][0], "/usr/bin/ls")
        self.assertEqual(planned["commands"], ["/usr/bin/ls -l"])
        self.assertIn("odoo17 (run-as user of /opt/odoo17", planned["identity"])
        self.assertEqual(planned["raw"]["cwd"], "/opt/odoo17")

    def test_run_as_program_must_be_found(self):
        planned = asyncio.run(runner.plan_step({"op": "command", "argv": ["odp-no-such-x"], "as": "run-as",
                                                "installation": "/opt/odoo17"}, {}, FakeEnv()))
        self.assertFalse(planned["ok"])
        self.assertIn("absolute path", planned["checks"][0]["detail"])

    def test_retry_drops_derived_values(self):
        text = '[params.config]\nkind = "config"\n[[steps]]\nop = "command"\nargv = ["false"]\ninstallation = "{installation}"\n'
        workflow.save("derived", text)
        result = asyncio.run(runner.run("derived", {"config": "/opt/odoo17/conf/a.conf"}, FakeEnv(), lambda e: None,
                                        _yes))
        self.assertEqual(result["status"], "fail")
        self.assertIn("installation", runner.history()[0]["params"])
        point = runner.retry_point(result["run_id"])
        self.assertEqual(point["params"], {"config": "/opt/odoo17/conf/a.conf"})
        asyncio.run(runner.preview("derived", point["params"], FakeEnv(), point["start"]))

    def test_unknown_installation_is_a_failed_check(self):
        planned = asyncio.run(runner.plan_step({"op": "db.snapshot", "installation": "/opt/none", "database": "d"},
                                               {}, FakeEnv()))
        self.assertFalse(planned["ok"])
        self.assertIn("not a discovered", planned["checks"][0]["detail"])


class CliAndSidecarTest(Fixture):
    def test_cli_list_preview_run(self):
        env = core_env(ODP_CONFIG_DIR=str(self.tmp / "cfg"), ODP_STATE_DIR=str(self.tmp / "state"),
                       ODP_SOCKET_DIR=str(self.tmp))
        workflow.save("cmds", COMMANDS)

        def odp(*args, stdin=""):
            return subprocess.run([sys.executable, "-m", "odoo_dev_panel", "tasks", *args], input=stdin,
                                  capture_output=True, text=True, env=env, timeout=60)

        out = odp("list")
        self.assertIn("safe-upgrade", out.stdout)
        self.assertIn("cmds", out.stdout)
        out = odp("preview", "cmds", "-p", "word=x y")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("$ echo 'x y'", out.stdout)
        self.assertIn("[waits for your confirmation]", out.stdout)
        out = odp("run", "cmds", "-p", "bad=1")
        self.assertEqual(out.returncode, 2)
        self.assertIn("unknown parameter bad", out.stderr)
        out = odp("run", "cmds", stdin="y\ny\nn\n")
        self.assertEqual(out.returncode, 1)
        self.assertIn("declined", out.stdout)
        self.assertIn("odp tasks retry", out.stdout)
        run_id = out.stdout.strip().split("run ")[-1].split(",")[0]
        out = odp("retry", run_id, "--yes")
        self.assertIn("from step 2", out.stdout)
        out = odp("history", "--json")
        rows = json.loads(out.stdout)
        self.assertEqual(rows[0]["retry_of"], run_id)

    def test_sidecar_gate_confirm_and_finish(self):
        workflow.save("cmds", COMMANDS.replace("test -e {word}", "true"))
        proc = subprocess.Popen(
            [sys.executable, "-m", "odoo_dev_panel", "sidecar"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=core_env(ODP_SOCKET_DIR=str(self.tmp), XDG_RUNTIME_DIR=str(self.tmp),
                         ODP_CONFIG_DIR=str(self.tmp / "cfg"), ODP_STATE_DIR=str(self.tmp / "state")))
        try:
            def call(id_, method, params):
                proc.stdin.write(frame({"jsonrpc": "2.0", "id": id_, "method": method, "params": params}))
                proc.stdin.flush()

            def until(pred):
                while True:
                    msg = read_frame(proc.stdout)
                    if pred(msg):
                        return msg

            call(1, "tasks.preview", {"name": "cmds"})
            shown = until(lambda m: m.get("id") == 1)["result"]
            self.assertEqual(shown["gates"], 2)
            call(2, "tasks.run", {"name": "cmds"})
            run_id = until(lambda m: m.get("id") == 2)["result"]["run_id"]
            for step in (0, 1):
                gate = until(lambda m: m.get("method") == "tasks.step" and m["params"].get("status") == "gate")
                self.assertEqual(gate["params"]["task_step"], step)
                self.assertIn("plan", gate["params"])
                call(10 + step, "tasks.confirm", {"run_id": run_id, "step": step, "approve": True})
            done = until(lambda m: m.get("method") == "tasks.finished")["params"]
            self.assertTrue(done["ok"])
            self.assertEqual(done["status"], "ok")
            call(3, "tasks.history", {})
            rows = until(lambda m: m.get("id") == 3)["result"]
            self.assertEqual(rows[0]["run"], run_id)
            call(4, "tasks.confirm", {"run_id": run_id, "step": 0, "approve": True})
            self.assertIn("error", until(lambda m: m.get("id") == 4))
        finally:
            proc.stdin.close()
            proc.wait(10)
            proc.stdout.close()


if __name__ == "__main__":
    unittest.main()
