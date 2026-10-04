import unittest

from odoo_dev_panel.discover.model import Installation, Instance
from odoo_dev_panel.rpc import RpcError
from odoo_dev_panel.run import RunSpec, build_argv, pick_port, plan, resolve

INST = Installation(root="/opt/odoo19", source="/opt/odoo19/odoo", version="19.0", owner="odoo19",
                    venv="/opt/odoo19/venv", venv_python="/opt/odoo19/venv/bin/python")


def instance(**options):
    return Instance(path="/etc/odoo/odoo19/client_a.conf", name="client_a", installation="/opt/odoo19",
                    link="addons_path", options=options)


class BuildArgv(unittest.TestCase):
    def test_plain(self):
        argv = build_argv(INST, instance(), RunSpec())
        self.assertEqual(argv, ["/opt/odoo19/venv/bin/python", "/opt/odoo19/odoo/odoo-bin", "-c", "/etc/odoo/odoo19/client_a.conf"])

    def test_all_flags(self):
        spec = RunSpec.from_params({"db": "client_a_dev", "update": "sale,crm", "install": ["web_x"],
                                    "stop_after_init": True, "dev": ["reload", "xml"], "extra": ["--log-level=debug"]})
        argv = build_argv(INST, instance(), spec, port=8070)
        self.assertEqual(argv[4:], ["-d", "client_a_dev", "--http-port", "8070", "-u", "sale,crm", "-i", "web_x",
                                    "--stop-after-init", "--dev=reload,xml", "--log-level=debug"])

    def test_update_does_not_force_stop(self):
        argv = build_argv(INST, instance(), RunSpec.from_params({"db": "d", "update": ["sale"]}))
        self.assertNotIn("--stop-after-init", argv)

    def test_kind(self):
        self.assertEqual(RunSpec().kind, "serve")
        self.assertEqual(RunSpec(db="d", update=["a"]).kind, "upgrade")
        self.assertEqual(RunSpec(db="d", install=["a"]).kind, "install")

    def test_no_venv(self):
        bare = Installation(root="/x", source="/x/odoo", version=None, owner=None)
        with self.assertRaises(RpcError):
            build_argv(bare, instance(), RunSpec())

    def test_validation(self):
        for bad in ({"update": "sale"}, {"db": "a b"}, {"db": "d", "update": "x;rm"}, {"dev": ["nope"]},
                    {"http_port": 0}, {"http_port": "80"}, {"extra": [1]}):
            with self.assertRaises(RpcError, msg=bad):
                RunSpec.from_params(bad)


class Ports(unittest.TestCase):
    def test_requested_wins(self):
        self.assertEqual(pick_port(instance(http_port="8069"), 9000, busy={9000}), 9000)

    def test_conf_port_free(self):
        self.assertEqual(pick_port(instance(http_port="8071"), busy={8069}), 8071)

    def test_next_free(self):
        self.assertEqual(pick_port(instance(http_port="8069"), busy={8069, 8070}), 8071)

    def test_default_and_xmlrpc(self):
        self.assertEqual(pick_port(instance(), busy=set()), 8069)
        self.assertEqual(pick_port(instance(xmlrpc_port="8100"), busy=set()), 8100)


SNAP = {
    "installations": [{"root": "/opt/odoo19", "source": "/opt/odoo19/odoo", "version": "19.0", "owner": "odoo19",
                       "venv": "/opt/odoo19/venv", "venv_python": "/opt/odoo19/venv/bin/python", "venv_ok": True,
                       "adopted": False, "name": "odoo19"}],
    "instances": [
        {"path": "/etc/odoo/odoo19/client_a.conf", "name": "client_a", "installation": "/opt/odoo19", "link": "path", "options": {"http_port": "8069"}},
        {"path": "/etc/odoo/odoo19/a.conf", "name": "dup", "installation": "/opt/odoo19", "link": "path", "options": {}},
        {"path": "/etc/odoo/odoo18/a.conf", "name": "dup", "installation": "/opt/odoo19", "link": "path", "options": {}},
        {"path": "/etc/odoo/x/orphan.conf", "name": "orphan", "installation": None, "link": None, "options": {}},
    ],
}


class Plan(unittest.TestCase):
    def test_serve_picks_free_port(self):
        p = plan(SNAP, "client_a", {"db": "client_a"}, busy={8069})
        self.assertEqual(p["user"], "odoo19")
        self.assertEqual(p["meta"]["port"], 8070)
        self.assertEqual(p["meta"]["kind"], "serve")
        self.assertIn("--http-port", p["argv"])

    def test_one_shot_has_no_port(self):
        p = plan(SNAP, "client_a", {"db": "d", "update": ["sale"], "stop_after_init": True}, busy={8069})
        self.assertIsNone(p["meta"]["port"])
        self.assertNotIn("--http-port", p["argv"])
        self.assertIn("--stop-after-init", p["argv"])

    def test_shell(self):
        p = plan(SNAP, "client_a", {"db": "client_a", "shell": True}, busy=set())
        self.assertTrue(p["pty"])
        self.assertEqual(p["meta"]["kind"], "shell")
        self.assertIsNone(p["meta"]["port"])
        self.assertEqual(p["argv"][2:6], ["shell", "--shell-interface=python", "-c", "/etc/odoo/odoo19/client_a.conf"])
        for bad in ({"shell": True}, {"shell": True, "db": "d", "update": ["a"]}):
            with self.assertRaises(RpcError, msg=bad):
                plan(SNAP, "client_a", bad, busy=set())

    def test_resolve_errors(self):
        for ref in ("missing", "dup", "orphan"):
            with self.assertRaises(RpcError, msg=ref):
                resolve(SNAP, ref)

    def test_resolve_by_path(self):
        self.assertEqual(resolve(SNAP, "/etc/odoo/odoo19/a.conf")[1].name, "dup")


if __name__ == "__main__":
    unittest.main()
