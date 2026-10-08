import tempfile
import unittest
from pathlib import Path
from unittest import mock

from odoo_dev_panel import services

UNIT = """[Service]
User=odoo19
ExecStart=/opt/odoo19/venv/bin/python /opt/odoo19/odoo-bin -c /etc/odoo19.conf
"""
OTHER = "[Service]\nExecStart=/usr/bin/nginx\n"


class ServicesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        (Path(self.dir) / "odoo19.service").write_text(UNIT)
        (Path(self.dir) / "nginx.service").write_text(OTHER)
        self.dirs = (self.dir,)

    def test_only_odoo_units_are_found(self):
        self.assertEqual(services.find_unit("odoo19.service", self.dirs).user, "odoo19")
        with self.assertRaises(services.ServiceError):
            services.find_unit("nginx.service", self.dirs)
        with self.assertRaises(services.ServiceError):
            services.find_unit("../../etc/passwd", self.dirs)

    def test_read_unit_is_limited_to_odoo_units(self):
        self.assertIn("ExecStart", services.read_unit("odoo19.service", self.dirs)["text"])
        with self.assertRaises(services.ServiceError):
            services.read_unit("nginx.service", self.dirs)

    def test_action_command(self):
        self.assertEqual(services.action_command("restart", "odoo19.service"), ["sudo", "--", "systemctl", "restart", "odoo19.service"])
        self.assertEqual(services.action_command("stop", "a.service", askpass=True)[:3], ["sudo", "-A", "--"])
        with self.assertRaises(services.ServiceError):
            services.action_command("mask", "odoo19.service")

    def test_journal_command_clamps_lines(self):
        cmd = services.journal_command("odoo19.service", lines=10**9, since="1 hour ago")
        self.assertEqual(cmd[cmd.index("-n") + 1], str(services.MAX_LINES))
        self.assertEqual(cmd[-2:], ["--since", "1 hour ago"])
        self.assertEqual(services.journal_command("x.service", sudo=True)[:2], ["sudo", "--"])

    def test_run_action_refuses_unknown_unit_before_sudo(self):
        with mock.patch.object(services.units_mod, "find_units", return_value=[]), mock.patch.object(services.subprocess, "run") as run:
            with self.assertRaises(services.ServiceError):
                services.run_action("stop", "sshd.service")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
