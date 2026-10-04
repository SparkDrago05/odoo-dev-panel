import unittest
from unittest import mock

from odoo_dev_panel import privilege


class AgentCommandTest(unittest.TestCase):
    def test_systemd_runs_agent_as_own_system_service(self):
        cmd = privilege.agent_command("odoo99", askpass=True, use_systemd=True)
        self.assertEqual(cmd[:3], ["sudo", "-A", "--"])
        self.assertTrue(cmd[3].endswith("systemd-run"))
        self.assertIn("--uid=odoo99", cmd)
        self.assertIn("--property=KillMode=process", cmd)
        self.assertIn("--collect", cmd)
        self.assertTrue(any(a.startswith("--unit=odp-agent-odoo99-") for a in cmd))
        tail = cmd[cmd.index("agent"):]
        self.assertEqual(tail[:3], ["agent", "serve", "--foreground"])
        self.assertNotIn("-u", cmd, "no sudo -u: systemd-run switches the user")

    def test_unit_names_are_unique(self):
        names = {next(a for a in privilege.agent_command("odoo99", False, True) if a.startswith("--unit=")) for _ in range(20)}
        self.assertEqual(len(names), 20)

    def test_fallback_without_systemd(self):
        cmd = privilege.agent_command("odoo99", askpass=False, use_systemd=False)
        self.assertEqual(cmd[:4], ["sudo", "-u", "odoo99", "--"])
        self.assertNotIn("--foreground", cmd, "the fallback agent detaches itself")
        self.assertEqual(cmd[-4:-2], ["agent", "serve"])

    def test_autodetect(self):
        with mock.patch.object(privilege, "systemd_available", return_value=False):
            self.assertIn("-u", privilege.agent_command("odoo99", False))
        with mock.patch.object(privilege, "systemd_available", return_value=True):
            self.assertNotIn("-u", privilege.agent_command("odoo99", False))


if __name__ == "__main__":
    unittest.main()


class CandidateUsersTest(unittest.TestCase):
    def test_system_accounts_in_group_count_and_developers_do_not(self):
        from types import SimpleNamespace

        from odoo_dev_panel import client

        uids = {"erp17": 990, "odoo19": 995, "dev": 1000}
        with mock.patch.object(client, "agent_sockets", return_value={"sock_user": None}), \
                mock.patch.object(client, "_uid_min", return_value=1000), \
                mock.patch.object(client.grp, "getgrnam", return_value=SimpleNamespace(gr_mem=["erp17", "odoo19", "dev", "gone"])), \
                mock.patch.object(client.pwd, "getpwnam", side_effect=lambda n: SimpleNamespace(pw_uid=uids[n]) if n in uids else (_ for _ in ()).throw(KeyError(n))):
            users = client.candidate_users(extra={"found"})
        self.assertEqual(users, ["erp17", "found", "odoo19", "sock_user"])

    def test_uid_min_from_login_defs(self):
        import tempfile

        from odoo_dev_panel import client

        with tempfile.NamedTemporaryFile("w", suffix=".defs") as f:
            f.write("# comment\nUID_MIN\t\t 2000\nUID_MAX 60000\n")
            f.flush()
            self.assertEqual(client._uid_min(f.name), 2000)
        self.assertEqual(client._uid_min("/nonexistent/login.defs"), 1000)
