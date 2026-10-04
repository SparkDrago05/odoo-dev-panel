import os
import tempfile
import unittest
from pathlib import Path

from odoo_dev_panel.discover import configs, filestore, installs, ports, processes, units
from odoo_dev_panel.discover.model import Installation, Instance

RELEASE = "RELEASE_LEVELS = [ALPHA, BETA, RC, FINAL] = ['alpha', 'beta', 'candidate', 'final']\nversion_info = ({major}, 0, 0, FINAL, 0, '')\n"


def make_tree(base: Path, major: int, *, venv: str | None = "venv", nested: bool = True) -> Path:
    """nested: <base>/odoo is the clone (nested layout). Otherwise base itself is the clone."""
    source = base / "odoo" if nested else base
    (source / "odoo").mkdir(parents=True)
    (source / "odoo-bin").write_text("#!/usr/bin/env python3\n")
    (source / "odoo" / "release.py").write_text(RELEASE.format(major=major))
    if venv:
        (base / venv / "bin").mkdir(parents=True)
        (base / venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
        (base / venv / "bin" / "python").symlink_to("/usr/bin/python3")
    return source


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_version(self):
        src = make_tree(self.base / "o17", 17)
        self.assertEqual(installs.read_version(src), "17.0")

    def test_read_version_garbage(self):
        (self.base / "odoo").mkdir()
        (self.base / "odoo" / "release.py").write_text("nothing here")
        self.assertIsNone(installs.read_version(self.base))

    def test_nested_layout_root_is_parent(self):
        make_tree(self.base / "odoo19", 19)
        found = installs.scan_installations([self.base])
        self.assertEqual([i.root for i in found], [str(self.base / "odoo19")])
        self.assertEqual(found[0].source, str(self.base / "odoo19" / "odoo"))
        self.assertEqual(found[0].version, "19.0")
        self.assertTrue(found[0].venv_ok)

    def test_plain_clone_is_its_own_root(self):
        make_tree(self.base / "work" / "odoo", 18, venv=None, nested=False)
        found = installs.scan_installations([self.base])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].root, found[0].source)
        self.assertIsNone(found[0].venv_ok)

    def test_plain_clone_with_venv_inside(self):
        make_tree(self.base / "odoo", 16, venv=".venv", nested=False)
        found = installs.scan_installations([self.base])
        self.assertEqual(found[0].venv, str(self.base / "odoo" / ".venv"))

    def test_broken_venv_is_flagged(self):
        make_tree(self.base / "odoo15", 15)
        py = self.base / "odoo15" / "venv" / "bin" / "python"
        py.unlink()
        py.symlink_to(self.base / "gone")
        found = installs.scan_installations([self.base])
        self.assertIs(found[0].venv_ok, False)

    @unittest.skipIf(os.geteuid() == 0, "root can read everything")
    def test_unreadable_directory_is_reported(self):
        locked = self.base / "odoo99"
        locked.mkdir()
        locked.chmod(0)
        try:
            seen: list[str] = []
            self.assertEqual(installs.scan_installations([self.base], unreadable=seen), [])
            self.assertEqual(seen, [str(locked)])
        finally:
            locked.chmod(0o755)

    def test_depth_limit_and_skip(self):
        make_tree(self.base / "a" / "b" / "c" / "d" / "odoo17", 17)
        self.assertEqual(installs.scan_installations([self.base], max_depth=3), [])
        make_tree(self.base / "node_modules" / "odoo17", 17)
        self.assertEqual(installs.scan_installations([self.base], max_depth=3), [])

    def test_does_not_descend_into_an_odoo_tree(self):
        make_tree(self.base / "odoo19", 19)
        make_tree(self.base / "odoo19" / "odoo" / "nested", 18)
        self.assertEqual(len(installs.scan_installations([self.base])), 1)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        make_tree(self.base / "odoo19", 19)
        make_tree(self.base / "odoo18", 18)
        self.installs = installs.scan_installations([self.base])
        self.cfg = self.base / "etc" / "odoo19"
        self.cfg.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name: str, body: str, folder: Path | None = None) -> Path:
        path = (folder or self.cfg) / name
        path.write_text(body)
        return path

    def test_multiline_addons_path_and_comments(self):
        b = self.base
        path = self.write("a.conf", f"[options]\n; comment\naddons_path = {b}/odoo19/odoo/addons,\n\t{b}/odoo19/custom\ndb_user = odoo19\n")
        (b / "odoo19" / "odoo" / "addons").mkdir()
        (b / "odoo19" / "custom").mkdir()
        [inst] = configs.build_instances([path], self.installs)
        self.assertEqual(inst.installation, str(b / "odoo19"))
        self.assertEqual(inst.link, "addons_path")
        self.assertEqual(inst.problems, [])

    def test_secrets_are_dropped(self):
        b = self.base
        path = self.write("a.conf", f"[options]\naddons_path = {b}/odoo19/odoo/addons\nadmin_passwd = s3cret\ndb_password = hunter2\ndb_user = x\n")
        [inst] = configs.build_instances([path], self.installs)
        self.assertNotIn("admin_passwd", inst.options)
        self.assertNotIn("db_password", inst.options)
        self.assertNotIn("s3cret", repr(inst))
        self.assertEqual(inst.options["db_user"], "x")

    def test_non_odoo_conf_is_ignored(self):
        path = self.write("nginx.conf", "server {\n}\n")
        other = self.write("x.conf", "[main]\nk = v\n")
        self.assertEqual(configs.build_instances([path, other], self.installs), [])

    def test_mixed_installations_reported(self):
        b = self.base
        path = self.write("mix.conf", f"[options]\naddons_path = {b}/odoo19/odoo/addons,{b}/odoo19/custom,{b}/odoo18/odoo/addons\n")
        [inst] = configs.build_instances([path], self.installs)
        self.assertEqual(inst.installation, str(b / "odoo19"))
        self.assertTrue(any("mixes" in p for p in inst.problems))

    def test_orphan_with_version_hint(self):
        folder = self.base / "etc" / "odoo13"
        folder.mkdir()
        path = self.write("old.conf", "[options]\naddons_path = /opt/odoo13/addons\n", folder)
        [inst] = configs.build_instances([path], self.installs)
        self.assertIsNone(inst.installation)
        self.assertEqual(inst.version_hint, "13.0")

    def test_folder_name_links_config_without_addons_path(self):
        folder = self.base / "etc" / "odoo19"
        path = self.write("bare.conf", "[options]\ndb_user = odoo19\n", folder)
        [inst] = configs.build_instances([path], self.installs)
        self.assertEqual((inst.installation, inst.link), (str(self.base / "odoo19"), "path"))

    def test_role_filled_from_config(self):
        b = self.base
        path = self.write("a.conf", f"[options]\naddons_path = {b}/odoo19/odoo/addons\ndb_user = odoo19\n")
        configs.build_instances([path], self.installs)
        self.assertEqual(next(i for i in self.installs if i.version == "19.0").pg_role, "odoo19")

    def test_version_hint_names(self):
        self.assertEqual(configs.version_hint(Path("/etc/odoo/odoo15/x.conf")), "15.0")
        self.assertEqual(configs.version_hint(Path("/etc/odoo/odoo-17/x.conf")), "17.0")
        self.assertIsNone(configs.version_hint(Path("/etc/odoo/x.conf")))


class FilestoreTests(unittest.TestCase):
    def test_default_uses_home_of_run_as_user(self):
        base = filestore.filestore_base({}, "/opt/odoo19")
        self.assertEqual(filestore.filestore_path(base, "db"), "/opt/odoo19/.local/share/Odoo/filestore/db")

    def test_data_dir_wins(self):
        base = filestore.filestore_base({"data_dir": "/data/odoo"}, "/opt/odoo19")
        self.assertEqual(filestore.filestore_path(base, "db"), "/data/odoo/filestore/db")

    def test_unknown_home(self):
        self.assertIsNone(filestore.filestore_path(filestore.filestore_base({}, None), "db"))


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proc = Path(self.tmp.name) / "proc"
        self.proc.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, pid, argv, ppid=1, cwd=None):
        d = self.proc / str(pid)
        d.mkdir()
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        (d / "stat").write_text(f"{pid} (python) S {ppid} " + " ".join(["0"] * 17) + " 4242 0\n")
        if cwd:
            (d / "cwd").symlink_to(cwd)

    def test_is_odoo_argv(self):
        self.assertTrue(processes.is_odoo_argv(["/v/bin/python", "./odoo/odoo-bin", "-c", "x"]))
        self.assertTrue(processes.is_odoo_argv(["python3", "-m", "odoo", "-c", "x"]))
        self.assertTrue(processes.is_odoo_argv(["/usr/bin/odoo", "-c", "x"]))
        self.assertFalse(processes.is_odoo_argv(["vim", "odoo-bin"]))
        self.assertFalse(processes.is_odoo_argv([]))

    def test_link_by_config_with_flags(self):
        inst = Instance(path="/etc/odoo/odoo19/n.conf", name="n", installation="/opt/odoo19", link="addons_path", options={"http_port": "8070"})
        self.add(10, ["/opt/odoo19/venv/bin/python", "./odoo/odoo-bin", "-c", "/etc/odoo/odoo19/n.conf", "-d", "client_a", "--dev=xml"])
        [p] = processes.discover_processes([inst], [], str(self.proc))
        self.assertEqual((p.instance, p.installation, p.link, p.database), (inst.path, "/opt/odoo19", "config", "client_a"))
        self.assertEqual((p.port, p.port_source), (8070, "config"))

    def test_cli_port_beats_config_and_default_is_8069(self):
        self.add(10, ["python", "/opt/odoo19/odoo/odoo-bin", "--config=/x.conf", "--http-port", "8099"])
        self.add(11, ["python", "/opt/odoo19/odoo/odoo-bin"])
        a, b = processes.discover_processes([], [], str(self.proc))
        self.assertEqual((a.port, a.port_source), (8099, "cmdline"))
        self.assertEqual((b.port, b.port_source), (8069, "default"))

    def test_fallback_links_by_argv_and_by_cwd(self):
        inst = Installation(root="/opt/odoo17", source="/opt/odoo17/odoo", version="17.0", owner="odoo17")
        self.add(10, ["/opt/odoo17/venv/bin/python", "/opt/odoo17/odoo/odoo-bin"])
        self.add(11, ["python", "./odoo/odoo-bin"], cwd="/opt/odoo17")
        a, b = processes.discover_processes([], [inst], str(self.proc))
        self.assertEqual((a.installation, a.link), ("/opt/odoo17", "argv"))
        self.assertEqual((b.installation, b.link), ("/opt/odoo17", "cwd"))

    def test_relative_config_resolved_with_cwd(self):
        inst = Instance(path="/srv/o/odoo.conf", name="odoo", installation="/srv/o", link="path")
        self.add(10, ["python", "odoo-bin", "-c", "odoo.conf"], cwd="/srv/o")
        [p] = processes.discover_processes([inst], [], str(self.proc))
        self.assertEqual(p.instance, "/srv/o/odoo.conf")

    def test_workers_are_not_listed(self):
        self.add(10, ["python", "odoo-bin"], ppid=1)
        self.add(11, ["python", "odoo-bin"], ppid=10)
        self.assertEqual([p.pid for p in processes.list_processes(str(self.proc))], [10])


class PortTests(unittest.TestCase):
    def test_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            net = Path(tmp) / "net"
            net.mkdir()
            (net / "tcp").write_text(
                "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
                "   0: 00000000:1F95 00000000:0000 0A 00000000:00000000 00:00000000 00000000  0 0 555 1\n"
            )
            P = processes.OdooProcess
            procs = [P(pid=1, user=None, argv=[], port=8085), P(pid=2, user=None, argv=[], port=8085), P(pid=3, user=None, argv=[], port=9000)]
            result = ports.analyse(procs, tmp)
            self.assertEqual(result["listening"], [8085])
            self.assertEqual([(c["port"], c["kind"]) for c in result["conflicts"]], [(8085, "shared")])
            self.assertEqual([o["listening"] for o in result["odoo"]], [True, True, False])


class UnitTests(unittest.TestCase):
    def test_find_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "odoo.service").write_text(
                "[Service]\nUser=odoo15\nExecStart=/opt/odoo15/venv/bin/python /opt/odoo15/odoo/odoo-bin -c /etc/odoo/odoo15/d.conf\n"
            )
            Path(tmp, "nginx.service").write_text("[Service]\nExecStart=/usr/sbin/nginx\n")
            [u] = units.find_units((tmp,))
            self.assertEqual((u.name, u.user, u.config), ("odoo.service", "odoo15", "/etc/odoo/odoo15/d.conf"))


class DatabaseTests(unittest.TestCase):
    def test_databases_with_filestore_and_fallback_to_next_config(self):
        from odoo_dev_panel.discover import databases

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".local/share/Odoo/filestore/shop").mkdir(parents=True)
            inst = Installation(root="/opt/o", source="/opt/o/odoo", version="19.0", owner="o", home=str(home))
            bad = Instance(path="bad.conf", name="bad", installation="/opt/o", link="path", options={})
            good = Instance(path="good.conf", name="good", installation="/opt/o", link="path", options={})
            confs = {"bad.conf": {"db_user": "x", "db_password": "wrong"}, "good.conf": {"db_user": "x", "db_password": "ok", "db_port": "5433"}}
            calls = []

            def reader(host, port, user, password):
                calls.append((host, port, password))
                if password != "ok":
                    raise RuntimeError("password authentication failed")
                return [("shop", 10), ("blog", 5)]

            [out] = databases.discover_databases([inst], [bad, good], reader, confs.get)
            self.assertEqual([c[2] for c in calls], ["wrong", "ok"])
            self.assertEqual(out["via"], "good.conf")
            self.assertEqual([(d["name"], d["filestore_exists"]) for d in out["databases"]], [("shop", True), ("blog", False)])
            self.assertNotIn("ok", repr({k: v for k, v in out.items() if k != "via"}))

    def test_no_login_reports_error(self):
        from odoo_dev_panel.discover import databases

        inst = Installation(root="/opt/o", source="/opt/o/odoo", version="19.0", owner="o")
        [out] = databases.discover_databases([inst], [], lambda *a: [], lambda p: {})
        self.assertEqual(out["databases"], [])
        self.assertTrue(out["error"])


def tree_digest(base: Path) -> dict:
    return {str(p): (p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(base.rglob("*")) if p.is_file() or p.is_symlink()}


class RegistryAndScanTests(unittest.TestCase):
    """A synthetic plain layout: a plain clone under a home directory, config next to it, no /etc/odoo."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.clone = self.base / "home" / "dev" / "src" / "odoo"
        make_tree(self.clone, 17, venv=".venv", nested=False)
        (self.clone / "addons").mkdir()
        (self.clone / "odoo.conf").write_text(
            f"[options]\naddons_path = {self.clone}/addons\ndb_user = dev\ndb_password = pw\nhttp_port = 8070\n"
        )
        self.reg = self.base / "state" / "registry.json"

    def tearDown(self):
        self.tmp.cleanup()

    def scan(self):
        from odoo_dev_panel.discover import scan

        return scan.scan([self.base / "home"], with_databases=False, proc_root=str(self.base / "noproc"), registry_file=self.reg, system=False)

    def test_scan_finds_plain_layout(self):
        snap = self.scan()
        [inst] = snap["installations"]
        self.assertEqual((inst["root"], inst["version"], inst["adopted"]), (str(self.clone), "17.0", False))
        [conf] = snap["instances"]
        self.assertEqual((conf["name"], conf["installation"], conf["options"]["http_port"]), ("odoo", str(self.clone), "8070"))
        self.assertNotIn("db_password", conf["options"])
        self.assertEqual(snap["processes"], [])

    def test_adopt_changes_only_the_registry(self):
        from odoo_dev_panel.discover import registry

        before = tree_digest(self.base / "home")
        [inst] = self.scan()["installations"]
        entry = registry.adopt(inst, "my17", self.reg)
        self.assertEqual(tree_digest(self.base / "home"), before)
        self.assertEqual(entry["name"], "my17")
        self.assertEqual(oct(self.reg.stat().st_mode & 0o777), "0o600")
        self.assertNotIn("db_password", self.reg.read_text())
        self.assertNotIn('"pw"', self.reg.read_text())
        [inst] = self.scan()["installations"]
        self.assertEqual((inst["adopted"], inst["name"]), (True, "my17"))

    def test_adopt_twice_keeps_first_timestamp_and_name(self):
        from odoo_dev_panel.discover import registry

        [inst] = self.scan()["installations"]
        first = registry.adopt(inst, "a", self.reg)
        second = registry.adopt(inst, None, self.reg)
        self.assertEqual((second["name"], second["adopted_at"]), ("a", first["adopted_at"]))

    def test_release_and_missing(self):
        import shutil

        from odoo_dev_panel.discover import registry

        [inst] = self.scan()["installations"]
        registry.adopt(inst, None, self.reg)
        shutil.rmtree(self.clone)
        snap = self.scan()
        self.assertEqual([m["root"] for m in snap["missing"]], [str(self.clone)])
        self.assertTrue(registry.release(str(self.clone), self.reg))
        self.assertFalse(registry.release(str(self.clone), self.reg))

    def test_corrupt_registry_does_not_break_scan(self):
        self.reg.parent.mkdir(parents=True)
        self.reg.write_text("{nope")
        snap = self.scan()
        self.assertIn("registry_error", snap)
        self.assertEqual(len(snap["installations"]), 1)


if __name__ == "__main__":
    unittest.main()
