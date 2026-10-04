"""Lifecycle tests: the agent runs as the current user in temporary directories."""

import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from odoo_dev_panel import rpc

from .helpers import FAKE_ODOO, AgentProcess, core_env, pid_alive, wait_for


class AgentTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "state").mkdir()
        self.agent = AgentProcess(self.root)
        self.agent.start()
        self._cleanup_pgids = []

    def tearDown(self):
        for pgid in self._cleanup_pgids:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.agent.stop()
        self._tmp.cleanup()

    def call(self, method, params=None, timeout=30):
        async def go():
            conn = await rpc.open_unix(str(self.agent.socket_path))
            try:
                return await conn.request(method, params, timeout=timeout)
            finally:
                await conn.close()

        return asyncio.run(go())

    def start_fake(self, *extra):
        session = self.call(
            "session.start", {"argv": [sys.executable, str(FAKE_ODOO), "--workers", "2", *extra], "name": "fake"}
        )
        self._cleanup_pgids.append(session["pgid"])
        return session

    def worker_pids(self, session):
        def found():
            text = Path(session["log_path"]).read_text()
            pids = [int(p) for p in re.findall(r"Worker WorkerHTTP \((\d+)\) alive", text)]
            return pids if len(pids) == 2 else None

        return wait_for(found, 10, "worker pids in log")


class SessionLifecycleTest(AgentTestCase):
    def test_start_follow_stop_kills_whole_group(self):
        session = self.start_fake()
        self.assertEqual(session["state"], "running")
        workers = self.worker_pids(session)

        async def follow():
            chunks = []
            got = asyncio.Event()

            async def on_output(params, conn):
                chunks.append(params["data"])
                if "werkzeug" in "".join(chunks) or "odoo." in "".join(chunks):
                    got.set()

            conn = await rpc.open_unix(str(self.agent.socket_path), {"session.output": on_output})
            await conn.request("session.follow", {"id": session["id"], "offset": 0})
            await asyncio.wait_for(got.wait(), 5)
            await conn.close()
            return "".join(chunks)

        output = asyncio.run(follow())
        self.assertIn("Odoo version 19.0 (fake)", output)

        unrelated = subprocess.Popen(["sleep", "30"], start_new_session=True)
        try:
            stopped = self.call("session.stop", {"id": session["id"], "timeout": 10})
            self.assertEqual(stopped["state"], "exited")
            self.assertEqual(stopped["exit_code"], 0)
            self.assertFalse(pid_alive(session["pid"]))
            wait_for(lambda: not any(pid_alive(p) for p in workers), 5, "workers to exit")
            self.assertTrue(pid_alive(unrelated.pid), "an unrelated process must survive Stop")
        finally:
            unrelated.kill()
            unrelated.wait()
        self.assertIn("Initiating shutdown", Path(session["log_path"]).read_text())

    def test_sigkill_escalation(self):
        session = self.start_fake("--ignore-term")
        self.worker_pids(session)
        stopped = self.call("session.stop", {"id": session["id"], "timeout": 1})
        self.assertEqual(stopped["state"], "exited")
        self.assertEqual(stopped["exit_code"], -signal.SIGKILL)

    def test_exit_code_recorded(self):
        session = self.call(
            "session.start", {"argv": [sys.executable, str(FAKE_ODOO), "--exit-after", "0.3"]}
        )
        wait_for(lambda: self.call("session.get", {"id": session["id"]})["state"] == "exited", 10, "exit")
        self.assertEqual(self.call("session.get", {"id": session["id"]})["exit_code"], 3)

    def test_bad_argv_rejected(self):
        for argv in ([], ["relative/python"], ["/nonexistent/bin"]):
            with self.assertRaises(rpc.RpcError) as ctx:
                self.call("session.start", {"argv": argv})
            self.assertEqual(ctx.exception.code, rpc.INVALID_PARAMS)


class AgentCrashTest(AgentTestCase):
    def test_session_survives_agent_crash_and_is_readopted(self):
        session = self.start_fake()
        workers = self.worker_pids(session)
        log = Path(session["log_path"])

        self.agent.kill9()
        size = log.stat().st_size
        time.sleep(1.5)
        self.assertTrue(pid_alive(session["pid"]))
        self.assertTrue(all(pid_alive(p) for p in workers))
        self.assertGreater(log.stat().st_size, size, "process must keep writing its log without the agent")

        self.agent.start()
        listed = {s["id"]: s for s in self.call("session.list")}
        self.assertEqual(listed[session["id"]]["state"], "running")
        self.assertTrue(listed[session["id"]]["adopted"])

        stopped = self.call("session.stop", {"id": session["id"], "timeout": 10})
        self.assertEqual(stopped["state"], "exited")
        self.assertIsNone(stopped["exit_code"], "exit code of an adopted process is unknown")
        self.assertFalse(pid_alive(session["pid"]))
        wait_for(lambda: not any(pid_alive(p) for p in workers), 5, "workers to exit")

    def test_pid_reuse_guard(self):
        session = self.start_fake()
        self.agent.kill9()
        # Simulate a recycled pid: the stored start time no longer matches the live process.
        path = Path(session["log_path"]).parent / "session.json"
        data = json.loads(path.read_text())
        data["starttime"] += 1
        path.write_text(json.dumps(data))

        self.agent.start()
        listed = {s["id"]: s for s in self.call("session.list")}
        self.assertEqual(listed[session["id"]]["state"], "lost")
        self.call("session.stop", {"id": session["id"], "timeout": 1})
        self.assertTrue(pid_alive(session["pid"]), "a process we cannot prove is ours must never be signalled")

    def test_graceful_agent_shutdown_leaves_sessions_running(self):
        session = self.start_fake()
        self.call("agent.shutdown")
        wait_for(lambda: self.agent.proc.poll() is not None, 5, "agent exit")
        self.assertFalse(self.agent.socket_path.exists())
        self.assertTrue(pid_alive(session["pid"]))

    def test_shutdown_with_client_still_connected(self):
        """The app keeps its agent connection open; Stop agent must still end the agent."""

        async def go():
            idle = await rpc.open_unix(str(self.agent.socket_path))
            stopper = await rpc.open_unix(str(self.agent.socket_path))
            await stopper.request("agent.shutdown")
            await asyncio.wait_for(idle.closed.wait(), 5)
            await stopper.close()

        asyncio.run(go())
        wait_for(lambda: self.agent.proc.poll() is not None, 5, "agent exit with a client connected")


class PtyTest(AgentTestCase):
    def start_console(self):
        session = self.call("session.start", {
            "argv": [sys.executable, "-q", "-i", "-c", "import sys; print('tty', sys.stdin.isatty())"],
            "pty": True, "rows": 30, "cols": 100, "name": "console",
        })
        self._cleanup_pgids.append(session["pgid"])
        return session

    def log(self, session):
        return Path(session["log_path"]).read_text(errors="replace")

    def test_input_output_and_ctrl_d(self):
        session = self.start_console()
        self.assertTrue(session["pty"])
        wait_for(lambda: "tty True" in self.log(session), 10, "console banner")
        self.call("session.write", {"id": session["id"], "data": "import shutil; print(6 * 7, shutil.get_terminal_size())\n"})
        wait_for(lambda: "42 os.terminal_size(columns=100, lines=30)" in self.log(session), 10, "console answer")
        self.call("session.resize", {"id": session["id"], "rows": 50, "cols": 150})
        self.call("session.write", {"id": session["id"], "data": "print(shutil.get_terminal_size())\n"})
        wait_for(lambda: "columns=150, lines=50" in self.log(session), 10, "resized terminal")
        self.call("session.write", {"id": session["id"], "data": "\x04"})
        wait_for(lambda: self.call("session.get", {"id": session["id"]})["state"] == "exited", 10, "console exit")
        self.assertEqual(self.call("session.get", {"id": session["id"]})["exit_code"], 0)
        with self.assertRaises(rpc.RpcError) as ctx:
            self.call("session.write", {"id": session["id"], "data": "x\n"})
        self.assertEqual(ctx.exception.code, rpc.CONFLICT)

    def test_stop_pty_session(self):
        session = self.start_console()
        wait_for(lambda: "tty True" in self.log(session), 10, "console banner")
        stopped = self.call("session.stop", {"id": session["id"], "timeout": 5})
        self.assertEqual(stopped["state"], "exited")
        self.assertFalse(pid_alive(session["pid"]))

    def test_write_needs_pty(self):
        session = self.start_fake()
        with self.assertRaises(rpc.RpcError) as ctx:
            self.call("session.write", {"id": session["id"], "data": "x\n"})
        self.assertEqual(ctx.exception.code, rpc.CONFLICT)

    def test_pty_session_readopted_read_only(self):
        session = self.start_console()
        wait_for(lambda: "tty True" in self.log(session), 10, "console banner")
        self.agent.kill9()
        self.agent.start()
        again = self.call("session.get", {"id": session["id"]})
        if again["state"] == "running":  # the shell may also die of SIGHUP when the master closes
            with self.assertRaises(rpc.RpcError) as ctx:
                self.call("session.write", {"id": session["id"], "data": "x\n"})
            self.assertEqual(ctx.exception.code, rpc.CONFLICT)


class DetachTest(unittest.TestCase):
    def test_detached_start_reports_ready_and_parent_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, "-m", "odoo_dev_panel", "agent", "serve", "--socket-dir", f"{tmp}/run",
                 "--state-dir", f"{tmp}/state", "--allow-group", ""],
                env=core_env(), capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("agent started", result.stdout)
            agent_pid = int(result.stdout.split()[2])
            sock = next(Path(tmp, "run").glob("*.sock"))

            async def shutdown():
                conn = await rpc.open_unix(str(sock))
                info = await conn.request("agent.info")
                await conn.request("agent.shutdown")
                await conn.close()
                return info

            info = asyncio.run(shutdown())
            self.assertEqual(info["pid"], agent_pid)
            self.assertNotEqual(os.getsid(agent_pid), os.getsid(0), "agent must run in its own session")
            wait_for(lambda: not pid_alive(agent_pid), 5, "detached agent exit")

            second = subprocess.run(
                [sys.executable, "-m", "odoo_dev_panel", "agent", "serve", "--socket-dir", f"{tmp}/run",
                 "--state-dir", f"{tmp}/state", "--allow-group", ""],
                env=core_env(), capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            # A second agent for the same user while the first is alive must be refused.
            third = subprocess.run(
                [sys.executable, "-m", "odoo_dev_panel", "agent", "serve", "--socket-dir", f"{tmp}/run",
                 "--state-dir", f"{tmp}/state", "--allow-group", ""],
                env=core_env(), capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(third.returncode, 1)
            self.assertIn("already listening", third.stderr)
            asyncio.run(_shutdown(sock))


async def _shutdown(sock):
    conn = await rpc.open_unix(str(sock))
    await conn.request("agent.shutdown")
    await conn.close()


if __name__ == "__main__":
    unittest.main()
