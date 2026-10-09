import os
import tempfile
import unittest
from pathlib import Path

from odoo_dev_panel import extlogs


def snap(options, units=()):
    return {"instances": [{"path": "/etc/odoo/a.conf", "name": "a", "installation": "/opt/odoo19", "options": options}],
            "units": list(units)}


class SourceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log = Path(self._tmp.name) / "odoo.log"
        self.log.write_text("line\n")

    def tearDown(self):
        self._tmp.cleanup()

    def test_logfile(self):
        self.assertEqual(extlogs.source(snap({"logfile": str(self.log)}), "/etc/odoo/a.conf"),
                         {"kind": "logfile", "path": str(self.log), "unit": None, "reason": None})

    def test_unit_when_no_logfile_and_reasons(self):
        unit = {"name": "odoo19.service", "config": "/etc/odoo/a.conf"}
        self.assertEqual(extlogs.source(snap({}, [unit]), "/etc/odoo/a.conf")["unit"], "odoo19.service")
        self.assertIn("sets no logfile", extlogs.source(snap({"logfile": "False"}), "/etc/odoo/a.conf")["reason"])
        self.assertIn("relative", extlogs.source(snap({"logfile": "odoo.log"}), "/etc/odoo/a.conf")["reason"])
        self.assertIn("No such file", extlogs.source(snap({"logfile": "/nope/x.log"}), "/etc/odoo/a.conf")["reason"])
        with self.assertRaises(extlogs.ExtLogError):
            extlogs.source(snap({}), "/etc/odoo/other.conf")

    @unittest.skipIf(os.geteuid() == 0, "root reads everything")
    def test_unreadable_falls_back_to_unit(self):
        self.log.chmod(0)
        src = extlogs.source(snap({"logfile": str(self.log)}), "/etc/odoo/a.conf")
        self.assertIsNone(src["kind"])
        self.assertIn("not readable by you", src["reason"])
        unit = {"name": "odoo19.service", "config": "/etc/odoo/a.conf"}
        self.assertEqual(extlogs.source(snap({"logfile": str(self.log)}, [unit]), "/etc/odoo/a.conf")["kind"], "journal")


class ReadTest(unittest.TestCase):
    def test_tail_offset_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "odoo.log"
            log.write_text("".join(f"line {n}\n" for n in range(100)))
            tail = extlogs.read(str(log), max_bytes=30)
            self.assertTrue(tail["text"].startswith("line "))  # starts at a line, not mid-line
            self.assertEqual(tail["offset"], log.stat().st_size)
            with log.open("a") as fh:
                fh.write("new\n")
            more = extlogs.read(str(log), tail["offset"])
            self.assertEqual(more["text"], "new\n")
            log.write_text("fresh\n")
            again = extlogs.read(str(log), more["offset"])
            self.assertTrue(again["rotated"])
            self.assertEqual(again["text"], "fresh\n")
            with self.assertRaises(extlogs.ExtLogError):
                extlogs.read(str(Path(tmp) / "missing.log"))


if __name__ == "__main__":
    unittest.main()
