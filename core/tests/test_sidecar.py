import json
import subprocess
import sys
import tempfile
import unittest

import os
import pwd
from pathlib import Path

from .helpers import FAKE_ODOO, AgentProcess, core_env


def frame(obj) -> bytes:
    body = json.dumps(obj).encode()
    return b"Content-Length: %d\r\n\r\n" % len(body) + body


def read_frame(stream) -> dict:
    length = None
    while True:
        line = stream.readline()
        if not line:
            raise EOFError
        line = line.strip()
        if not line:
            break
        name, _, value = line.partition(b":")
        if name.lower() == b"content-length":
            length = int(value)
    return json.loads(stream.read(length))


class SidecarStdioTest(unittest.TestCase):
    def test_stray_print_does_not_corrupt_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.Popen(
                [sys.executable, "-m", "odoo_dev_panel", "sidecar"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=core_env(ODP_SOCKET_DIR=tmp, XDG_RUNTIME_DIR=tmp),
            )
            try:
                for i in range(200):
                    proc.stdin.write(frame({"jsonrpc": "2.0", "id": 2 * i, "method": "debug.print",
                                            "params": {"text": "Content-Length: 99\r\n\r\n{garbage"}}))
                    proc.stdin.write(frame({"jsonrpc": "2.0", "id": 2 * i + 1, "method": "app.info"}))
                proc.stdin.flush()
                seen = {}
                for _ in range(400):
                    msg = read_frame(proc.stdout)
                    self.assertNotIn("error", msg, msg)
                    seen[msg["id"]] = msg["result"]
                self.assertEqual(len(seen), 400)
                self.assertTrue(all(seen[2 * i] is True for i in range(200)))
                self.assertIn("version", seen[1])
            finally:
                proc.stdin.close()
                proc.wait(10)
                stderr = proc.stderr.read()
                proc.stderr.close()
                proc.stdout.close()
            self.assertIn(b"{garbage", stderr)
            self.assertEqual(proc.returncode, 0)


class SidecarProvisionTest(unittest.TestCase):
    def test_plan_bad_input_and_run_preflight_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.Popen(
                [sys.executable, "-m", "odoo_dev_panel", "sidecar"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=core_env(ODP_SOCKET_DIR=tmp, XDG_RUNTIME_DIR=tmp),
            )
            try:
                def call(id_, method, params):
                    proc.stdin.write(frame({"jsonrpc": "2.0", "id": id_, "method": method, "params": params}))
                    proc.stdin.flush()

                call(1, "provision.plan", {"version": 17, "root": "/opt/odp-test-none-17", "run_as": "odpnone17"})
                call(2, "provision.plan", {"version": 99})
                call(3, "provision.plan", {"version": 17, "bogus": 1})
                results = {}
                while len(results) < 3:
                    msg = read_frame(proc.stdout)
                    results[msg["id"]] = msg
                plan = results[1]["result"]
                self.assertEqual([s["id"] for s in plan["steps"]][:2], ["root-script", "clone-odoo"])
                self.assertIn("SCRAM-SHA-256", plan["root_script"])
                self.assertIn("error", results[2])
                self.assertIn("unknown field", results[3]["error"]["message"])

                # the dev user is not the current user, so preflight fails: nothing is changed and sudo is never called
                call(4, "provision.run", {"version": 17, "root": "/opt/odp-test-none-17", "run_as": "odpnone17", "dev_user": "nobody-else"})
                events = []
                while True:
                    msg = read_frame(proc.stdout)
                    if "method" in msg:
                        events.append(msg)
                        if msg["method"] == "provision.finished":
                            break
                finished = events[-1]["params"]
                self.assertEqual(finished["error"], "preflight failed")
                self.assertTrue(any(e["method"] == "provision.step" and e["params"]["status"] == "fail" for e in events))
            finally:
                proc.stdin.close()
                proc.wait(10)
                proc.stdout.close()


class SidecarDoctorTest(unittest.TestCase):
    def test_doctor_and_repair_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.Popen(
                [sys.executable, "-m", "odoo_dev_panel", "sidecar"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=core_env(ODP_SOCKET_DIR=tmp, XDG_RUNTIME_DIR=tmp),
            )
            try:
                for id_, method, params in ((1, "doctor.run", {"no_databases": True}),
                                            (2, "repair.plan", {"root": "/nonexistent/odoo"}),
                                            (3, "repair.run", {}),
                                            (4, "repair.plan", {"root": "/x", "python": 3})):
                    proc.stdin.write(frame({"jsonrpc": "2.0", "id": id_, "method": method, "params": params}))
                proc.stdin.flush()
                results = {}
                while len(results) < 4:
                    msg = read_frame(proc.stdout)
                    results[msg["id"]] = msg
                report = results[1]["result"]
                self.assertEqual(set(report), {"findings", "counts", "not_checked"})
                self.assertTrue(all(f["why"] for f in report["findings"]))
                self.assertIn("not a discovered Odoo installation", results[2]["error"]["message"])
                self.assertIn("root is required", results[3]["error"]["message"])
                self.assertIn("python must be", results[4]["error"]["message"])
            finally:
                proc.stdin.close()
                proc.wait(10)
                proc.stdout.close()


class SidecarAgentTest(unittest.TestCase):
    """UI-level flow through the sidecar: list agents, start a session, receive forwarded output, stop."""

    def test_end_to_end_through_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state").mkdir()
            agent = AgentProcess(root)
            agent.start()
            user = pwd.getpwuid(os.getuid()).pw_name
            proc = subprocess.Popen(
                [sys.executable, "-m", "odoo_dev_panel", "sidecar"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=core_env(ODP_SOCKET_DIR=str(agent.socket_dir), XDG_RUNTIME_DIR=tmp),
            )
            next_id = iter(range(1, 1000))
            notifications = []

            def call(method, params=None):
                msg_id = next(next_id)
                proc.stdin.write(frame({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}))
                proc.stdin.flush()
                while True:
                    msg = read_frame(proc.stdout)
                    if msg.get("id") == msg_id:
                        self.assertNotIn("error", msg, msg)
                        return msg["result"]
                    notifications.append(msg)

            try:
                agents = call("agents.list")
                # Members of the real odoo-dev group may also be listed (as stopped).
                self.assertIn((user, "running"), [(a["user"], a["state"]) for a in agents])
                session = call("session.start", {"user": user, "argv": [sys.executable, str(FAKE_ODOO)]})
                call("session.follow", {"user": user, "id": session["id"], "offset": 0})
                while not any(n.get("method") == "session.output" for n in notifications):
                    notifications.append(read_frame(proc.stdout))
                output = next(n for n in notifications if n.get("method") == "session.output")
                self.assertEqual(output["params"]["user"], user)
                self.assertIn("Odoo version", output["params"]["data"])
                listed = call("sessions.list")
                self.assertEqual(listed[0]["id"], session["id"])
                stopped = call("session.stop", {"user": user, "id": session["id"]})
                self.assertEqual(stopped["state"], "exited")
            finally:
                proc.stdin.close()
                proc.wait(10)
                proc.stdout.close()
                agent.stop()


if __name__ == "__main__":
    unittest.main()
