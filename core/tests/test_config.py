import os
import pwd
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from odoo_dev_panel import configedit as ce
from odoo_dev_panel.doctor import permissions

ME = pwd.getpwuid(os.getuid()).pw_name

TEXT = """[options]
; comment
addons_path = {addons}
admin_passwd = adm1n
db_password = s3cret
db_user = odoo17
smtp_password = False
http_port = 8069
"""


class MaskTest(unittest.TestCase):
    def test_secrets_masked_and_restored(self):
        text = TEXT.format(addons="/x")
        masked = ce.mask(text)
        self.assertNotIn("adm1n", masked)
        self.assertNotIn("s3cret", masked)
        self.assertIn("smtp_password = False", masked, "no value: nothing to hide")
        self.assertEqual(ce.unmask(masked, text), text)

    def test_typed_value_replaces_the_secret(self):
        text = TEXT.format(addons="/x")
        edited = ce.mask(text).replace("db_password = ********", "db_password = new")
        out = ce.unmask(edited, text)
        self.assertIn("db_password = new", out)
        self.assertIn("admin_passwd = adm1n", out)

    def test_mask_without_a_value_in_the_file_is_refused(self):
        with self.assertRaises(ce.ConfigError):
            ce.unmask("[options]\ndb_password = ********\n", "[options]\n")


class ValidateTest(unittest.TestCase):
    def test_errors_and_warnings(self):
        with tempfile.TemporaryDirectory() as d:
            issues = ce.validate(f"[options]\naddons_path = {d},/nope\nhttp_port = 99999\nworkers = two\nhtp_port = 1\n")
        by = {(i.level, i.key) for i in issues}
        self.assertIn(("error", "http_port"), by)
        self.assertIn(("error", "workers"), by)
        self.assertIn(("warning", "htp_port"), by, "unknown option: typo")
        self.assertIn(("warning", "addons_path"), by)

    def test_no_options_section(self):
        self.assertEqual(ce.validate("[other]\na = 1\n")[0].level, "error")
        self.assertEqual(ce.validate("not a config")[0].level, "error")

    def test_port_clash_with_another_config(self):
        snap = {"installations": [], "instances": [{"path": "/b.conf", "options": {"http_port": "8069"}, "installation": None}]}
        issues = ce.validate("[options]\nhttp_port = 8069\n", snap, "/a.conf")
        self.assertTrue(any(i.key == "http_port" and "/b.conf" in i.text for i in issues))


class SetOptionsTest(unittest.TestCase):
    TEXT = "[options]\n; keep me\naddons_path = /a,\n    /b\nhttp_port = 8069\ndb_password = ********\n\n[other]\nhttp_port = 1\n"

    def test_replace_remove_add_keep_rest(self):
        out = ce.set_options(self.TEXT, {"http_port": "8070", "addons_path": "/c", "workers": "2", "db_password": None})
        self.assertEqual(out, "[options]\n; keep me\naddons_path = /c\nhttp_port = 8070\nworkers = 2\n\n"
                              "[other]\nhttp_port = 1\n")

    def test_removed_key_drops_its_continuation_lines(self):
        out = ce.set_options(self.TEXT, {"addons_path": None})
        self.assertNotIn("/b", out)
        self.assertIn("; keep me\nhttp_port = 8069", out)

    def test_masked_secret_survives_a_form_change_and_save(self):
        self.assertIn("db_password = ********", ce.set_options(self.TEXT, {"http_port": "1"}))

    def test_refused(self):
        with self.assertRaises(ce.ConfigError):
            ce.set_options("[other]\n", {"http_port": "1"})
        with self.assertRaises(ce.ConfigError):
            ce.set_options(self.TEXT, {"http_port": "1\nadmin_passwd = x"})
        with self.assertRaises(ce.ConfigError):
            ce.set_options(self.TEXT, {"a b": "1"})

    def test_no_trailing_newline(self):
        self.assertEqual(ce.set_options("[options]\nhttp_port = 1", {"workers": "2"}),
                         "[options]\nhttp_port = 1\nworkers = 2\n")

    def test_booleans_and_choices_warn(self):
        issues = ce.validate("[options]\nproxy_mode = yes\nlog_level = loud\nlist_db = True\n")
        self.assertEqual({(i.level, i.key) for i in issues}, {("warning", "proxy_mode"), ("warning", "log_level")})


class AddonsEntriesTest(unittest.TestCase):
    def test_states(self):
        with tempfile.TemporaryDirectory() as d:
            for sub in ("o17/odoo/addons", "o18/odoo/addons", "custom"):
                os.makedirs(os.path.join(d, sub))
            snap = {"installations": [{"root": f"{d}/o17", "source": f"{d}/o17/odoo", "version": "17.0", "owner": None},
                                      {"root": f"{d}/o18", "source": f"{d}/o18/odoo", "version": "18.0", "owner": None}],
                    "instances": [{"path": "/a.conf", "installation": f"{d}/o17", "options": {}}]}
            value = f"{d}/o17/odoo/addons,{d}/o18/odoo/addons,{d}/custom,{d}/gone"
            got = [(e["state"], e["version"]) for e in ce.addons_entries(value, snap, "/a.conf")]
            self.assertEqual(got, [("ok", "17.0"), ("other", "18.0"), ("ok", None), ("missing", None)])
            # A config discovery does not link: the installation most entries lie in.
            got = [e["state"] for e in ce.addons_entries(f"{d}/o18/odoo/addons,{d}/o18/odoo/addons", snap, "/new.conf")]
            self.assertEqual(got, ["ok", "ok"])
            form = ce.form("[options]\nhttp_port = 1\n", snap, "/a.conf", {"addons_path": f"{d}/custom"})
            self.assertEqual(form["options"], {"http_port": "1", "addons_path": f"{d}/custom"})
            self.assertEqual(ce.form("garbage", snap, "/a.conf")["options"], None)


class SaveTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "a.conf")
        Path(self.path).write_text(TEXT.format(addons=self.dir.name))
        os.chmod(self.path, 0o640)

    def test_save_keeps_secrets_inode_and_mode_and_backs_up(self):
        opened = ce.open_config(self.path)
        self.assertNotIn("s3cret", opened.text)
        ino = os.stat(self.path).st_ino
        result = ce.save(self.path, opened.text.replace("8069", "8070"), opened.sha)
        text = Path(self.path).read_text()
        self.assertIn("http_port = 8070", text)
        self.assertIn("db_password = s3cret", text)
        self.assertEqual(os.stat(self.path).st_ino, ino)
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), "0o640")
        self.assertEqual(oct(os.stat(result["backup"]).st_mode & 0o777), "0o600")
        self.assertIn("http_port = 8069", Path(result["backup"]).read_text())

    def test_refused_when_changed_since_open(self):
        opened = ce.open_config(self.path)
        Path(self.path).write_text(Path(self.path).read_text() + "workers = 2\n")
        with self.assertRaises(ce.ConfigError):
            ce.save(self.path, opened.text, opened.sha)

    def test_refused_on_errors_and_file_untouched(self):
        opened = ce.open_config(self.path)
        before = Path(self.path).read_text()
        with self.assertRaises(ce.ConfigError):
            ce.save(self.path, opened.text.replace("8069", "x"), opened.sha)
        self.assertEqual(Path(self.path).read_text(), before)
        self.assertEqual([f for f in os.listdir(self.dir.name) if ".bak-" in f], [])

    def test_no_change_no_backup(self):
        opened = ce.open_config(self.path)
        self.assertFalse(ce.save(self.path, opened.text, opened.sha)["changed"])

    def test_copy(self):
        result = ce.copy(self.path, "client_b", home_layout=True)
        self.assertEqual(result["path"], os.path.join(self.dir.name, "client_b.conf"))
        self.assertEqual(oct(os.stat(result["path"]).st_mode & 0o777), "0o600")
        with self.assertRaises(ce.ConfigError):
            ce.copy(self.path, "client_b", home_layout=True)
        with self.assertRaises(ce.ConfigError):
            ce.copy(self.path, "../evil", home_layout=True)


class PermsPlanTest(unittest.TestCase):
    def test_plan_files_folders_and_shared(self):
        with tempfile.TemporaryDirectory() as d:
            own = os.path.join(d, "odoo17")
            mixed = os.path.join(d, "mixed")
            os.makedirs(own)
            os.makedirs(mixed)
            for p in (f"{own}/a.conf", f"{mixed}/b.conf", f"{mixed}/other.conf"):
                Path(p).write_text("[options]\n")
                os.chmod(p, 0o644)
            snap = {"installations": [{"root": "/opt/odoo17", "owner": "runas"}],
                    "instances": [{"path": f"{own}/a.conf", "installation": "/opt/odoo17"},
                                  {"path": f"{mixed}/b.conf", "installation": "/opt/odoo17"},
                                  {"path": f"{mixed}/other.conf", "installation": "/opt/odoo19"}]}
            fake_pw = mock.Mock(pw_gid=4242, pw_dir="/nonexistent")
            real_getgrgid = permissions.grp.getgrgid
            with mock.patch.object(permissions.pwd, "getpwnam", return_value=fake_pw), \
                    mock.patch.object(permissions.grp, "getgrgid",
                                      side_effect=lambda gid: mock.Mock(gr_name="runas") if gid == 4242 else real_getgrgid(gid)):
                plan = permissions.plan_config_perms(snap, "/opt/odoo17", dev_user="dev")
        targets = {c.path: c.target for c in plan.changes}
        self.assertEqual(targets[f"{own}/a.conf"], "dev:runas 0640")
        self.assertEqual(targets[f"{mixed}/b.conf"], "dev:runas 0640")
        self.assertEqual(targets[own], "dev:runas 2750")
        self.assertNotIn(mixed, targets, "a folder with another installation's config stays")
        self.assertNotIn(f"{mixed}/other.conf", targets)
        self.assertTrue(any("mixed" in n for n in plan.notes))
        self.assertIn(f"chmod -- 2750 {own}", plan.script)

    def test_home_layout_is_0600_and_no_folders(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".odoorc")
            Path(p).write_text("[options]\n")
            os.chmod(p, 0o644)
            snap = {"installations": [{"root": "/home/me/src/odoo", "owner": ME}],
                    "instances": [{"path": p, "installation": "/home/me/src/odoo"}]}
            plan = permissions.plan_config_perms(snap, "/home/me/src/odoo")
        self.assertEqual([c.target_mode for c in plan.changes], ["0600"])
        self.assertEqual([c.kind for c in plan.changes], ["file"])


if __name__ == "__main__":
    unittest.main()
