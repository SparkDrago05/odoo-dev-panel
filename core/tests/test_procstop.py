import asyncio
import subprocess
import sys
import unittest

from odoo_dev_panel import procutil


def fake_odoo(script="import time; time.sleep(60)"):
    # argv: python -c <script> odoo-bin  ->  looks like an Odoo process to the discovery
    return subprocess.Popen([sys.executable, "-c", script, "odoo-bin"])


class StopPidTest(unittest.TestCase):
    def test_term_stops(self):
        p = fake_odoo()
        try:
            out = asyncio.run(procutil.stop_odoo_pid(p.pid, procutil.proc_starttime(p.pid), 5))
            p.wait(5)
            self.assertTrue(out["stopped"])
            self.assertEqual(out["signal"], "TERM")
        finally:
            p.kill()

    def test_refuses_other_process_and_recycled_pid(self):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            with self.assertRaises(procutil.StopError):
                asyncio.run(procutil.stop_odoo_pid(p.pid, procutil.proc_starttime(p.pid), 1))
        finally:
            p.kill()
        q = fake_odoo()
        try:
            with self.assertRaises(procutil.StopError):
                asyncio.run(procutil.stop_odoo_pid(q.pid, 1, 1))
        finally:
            q.kill()

    def test_force_needed_for_stubborn_process(self):
        script = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('r', flush=True); time.sleep(60)"
        p = subprocess.Popen([sys.executable, "-c", script, "odoo-bin"], stdout=subprocess.PIPE)
        try:
            p.stdout.readline()
            start = procutil.proc_starttime(p.pid)
            soft = asyncio.run(procutil.stop_odoo_pid(p.pid, start, 1))
            self.assertFalse(soft["stopped"])
            hard = asyncio.run(procutil.stop_odoo_pid(p.pid, start, 1, force=True))
            p.wait(5)
            self.assertTrue(hard["stopped"])
            self.assertEqual(hard["signal"], "KILL")
        finally:
            p.kill()


if __name__ == "__main__":
    unittest.main()
