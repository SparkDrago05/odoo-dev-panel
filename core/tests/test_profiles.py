"""T1-T6: profiles, nested custom repositories, provision preflight, resume, bundles on existing installations, export."""

import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from odoo_dev_panel import cli, rpc
from odoo_dev_panel.git import api, ops, registry
from odoo_dev_panel.provision import execute, export, plan, preflight, profiles, service
from odoo_dev_panel.provision.spec import CustomRepo, ProvisionSpec, SpecError, spec_from_dict
from odoo_dev_panel.sidecar import Sidecar
from tests.test_git import GitFixture, git
from tests.test_provision import FakeFacts

ORG = """name = "Org"
[install]
odoo_branch = "{series}"
[[repos]]
url = "git@git.example.com:org/core.git"
branch = "staging-{version}"
destination = "custom/org/core"
group = "org"
"""
EDU = """name = "Education"
odoo_version = 19
[config]
workers = 2
without_demo = "all"
[["repos+"]]
name = "admissions"
url = "git@example.com:edu/admissions.git"
branch = "staging-{version}"
destination = "custom/education/admissions"
"""


class ProfileFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {"ODP_CONFIG_DIR": str(self.tmp / "cfg"),
                                                 "ODP_STATE_DIR": str(self.tmp / "state")})
        self._env.start()
        (self.tmp / "cfg" / "profiles").mkdir(parents=True)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def write(self, name, text):
        path = self.tmp / "cfg" / ("org.toml" if name == "org" else f"profiles/{name}.toml")
        path.write_text(text)


class LayerTest(ProfileFixture):
    def test_layers_placeholders_and_origins(self):
        self.write("org", ORG)
        self.write("edu", EDU)
        r = profiles.resolve("edu")
        self.assertEqual(r["version"], 19)
        self.assertEqual(r["install"]["odoo_branch"], "19.0")
        self.assertEqual([x["destination"] for x in r["repos"]], ["custom/org/core", "custom/education/admissions"])
        self.assertEqual([x["branch"] for x in r["repos"]], ["staging-19", "staging-19"])
        self.assertEqual(r["origin"]["repos.0"], "org")
        self.assertEqual(r["origin"]["repos.1"], "profile:edu")
        self.assertEqual(r["origin"]["install.odoo_git"], "built-in")
        self.assertEqual(r["config"], {"workers": "2", "without_demo": "all"})

    def test_repos_replaces_and_wizard_wins(self):
        self.write("org", ORG)
        r = profiles.resolve(None, 18, {"install": {"root": "/srv/odoo18"}, "repos": [{"url": "https://h/x.git"}]})
        self.assertEqual(r["root"], "/srv/odoo18")
        self.assertEqual([x["url"] for x in r["repos"]], ["https://h/x.git"])
        self.assertEqual(r["origin"]["install.root"], "wizard")

    def test_pinned_version_and_unknown_placeholder(self):
        self.write("edu", EDU)
        with self.assertRaisesRegex(profiles.ProfileError, "is for Odoo 19"):
            profiles.resolve("edu", 18)
        self.write("bad", '[[repos]]\nurl = "https://h/{client}.git"\n')
        with self.assertRaisesRegex(profiles.ProfileError, "unknown placeholder"):
            profiles.resolve("bad", 17)
        with self.assertRaisesRegex(profiles.ProfileError, "choose an Odoo version"):
            profiles.resolve(None)

    def test_shape_errors(self):
        for text, msg in (('nope = 1', "unknown key"), ('[config]\nadmin_passwd = "x"', "cannot be set"),
                          ('[[repos]]\nurl = "https://u:p@h/x.git"', "password"), ('[install]\npg_port = "5432"', "wrong type"),
                          ('[[repos]]\nbranch = "x"', "url is required"), ('odoo_version = 14', "odoo_version")):
            with self.assertRaisesRegex(profiles.ProfileError, msg, msg=text):
                profiles.parse(text, "t")

    def test_dump_round_trip_save_import_delete(self):
        data = profiles.parse(EDU, "edu")
        text = profiles.dump(data)
        self.assertEqual(profiles.parse(text, "again"), data)
        tricky = {"name": 'quote " back \\ slash', "repos": [{"url": "https://h/x.git", "addons": False}]}
        self.assertEqual(profiles.parse(profiles.dump(tricky), "t"), tricky)
        path = profiles.save("edu", data)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(profiles.ProfileError, "exists"):
            profiles.save("edu", data)
        with self.assertRaises(profiles.ProfileError):
            profiles.save("../x", data)
        src = self.tmp / "shared.toml"
        src.write_text(EDU)
        self.assertTrue(profiles.import_file(str(src)).endswith("shared.toml"))
        moved = profiles.delete("edu")
        self.assertIn(".trash-edu-", moved)
        self.assertTrue(Path(moved).exists())
        self.assertEqual([p["name"] for p in profiles.list_profiles()], ["shared"])

    def test_org_path_setting(self):
        other = self.tmp / "elsewhere.toml"
        other.write_text(ORG)
        profiles.set_org_path(str(other))
        self.assertEqual(profiles.org_path(), other)
        self.assertEqual(profiles.resolve(None, 17)["repos"][0]["branch"], "staging-17")
        profiles.set_org_path(None)
        self.assertEqual(profiles.org_path().name, "org.toml")


class SpecTest(unittest.TestCase):
    def test_destinations(self):
        ok = ProvisionSpec(version=19, dev_user="alice", custom=[
            CustomRepo("https://h/a.git", "a", destination="custom/cms/admissions"),
            CustomRepo("https://h/b.git", "b")])
        self.assertEqual([c.destination for c in ok.custom], ["custom/cms/admissions", "custom/b"])
        for repos, msg in (([CustomRepo("https://h/a.git", "a", destination="../x")], "clean relative"),
                           ([CustomRepo("https://h/a.git", "a", destination="odoo/addons/x")], "provision manages"),
                           ([CustomRepo("https://h/a.git", "a", destination="custom/hr"),
                             CustomRepo("https://h/b.git", "b", destination="custom/hr/payroll")], "overlap"),
                           ([CustomRepo("https://h/a.git", "a", destination="/abs")], "clean relative")):
            with self.assertRaisesRegex(SpecError, msg):
                ProvisionSpec(version=19, dev_user="alice", custom=repos)

    def test_dict_repos_config_options_and_conf(self):
        s = spec_from_dict({"version": 19, "dev_user": "alice", "config_options": {"workers": "2"}, "custom": [
            {"url": "https://h/a.git", "destination": "custom/x/a", "addons": False},
            {"url": "https://h/themes.git", "purpose": "themes", "shallow": False}]})
        conf = plan.render_conf(s, plan.Secrets("p", "a"))
        self.assertNotIn("custom/x/a", conf)
        self.assertIn("/opt/odoo19/custom/themes", conf)
        self.assertIn("workers = 2", conf)
        self.assertIn("--single-branch", plan.custom_clone_command(s.custom[0], "/d"))
        self.assertNotIn("--depth", plan.custom_clone_command(s.custom[1], "/d"))
        with self.assertRaisesRegex(SpecError, "cannot be set"):
            spec_from_dict({"version": 19, "dev_user": "alice", "config_options": {"db_password": "x"}})


class FakeTreeFacts(FakeFacts):
    def tree(self, path, marker):
        return self.over.get("trees", {}).get(path, "missing")

    def realpath(self, path):
        return self.over.get("links", {}).get(path, path)


class PreflightTest(unittest.TestCase):
    def spec(self):
        return ProvisionSpec(version=19, dev_user="alice", root="/opt/odoo19", custom=[
            CustomRepo("git@h:a.git", "a", destination="custom/cms/a"), CustomRepo("git@h:b.git", "b")])

    def test_destination_states(self):
        f = FakeTreeFacts(paths={"/opt/odoo19", "/opt/odoo19/custom", "/opt/odoo19/custom/b", "/opt/odoo-dev-panel/python"},
                          trees={"/opt/odoo19/odoo": "present", "/opt/odoo19/custom/b": "foreign"})
        st = {c.id: c.status for c in preflight.destination_checks(self.spec(), f)}
        self.assertEqual(st, {"odoo": "warn", "repo:a": "ok", "repo:b": "fail"})

    def test_symlink_escape(self):
        f = FakeTreeFacts(paths={"/opt/odoo19", "/opt/odoo19/custom"}, links={"/opt/odoo19/custom": "/home/x"})
        st = {c.id: c.status for c in preflight.destination_checks(self.spec(), f)}
        self.assertEqual(st["repo:a"], "fail")

    def test_remote_checks(self):
        answers = {"https://github.com/odoo/odoo.git": ("ok", "19.0 found"), "git@h:a.git": ("auth", "Authentication failed"),
                   "git@h:b.git": ("network", "no answer")}
        f = FakeTreeFacts(trees={})
        st = {c.id: c.status for c in preflight.remote_checks(self.spec(), lambda url, ref: answers[url], f)}
        self.assertEqual(st, {"remote:odoo": "ok", "remote:a": "fail", "remote:b": "warn"})
        f = FakeTreeFacts(trees={"/opt/odoo19/odoo": "present"})
        ids = [c.id for c in preflight.remote_checks(self.spec(), lambda url, ref: ("ok", ""), f)]
        self.assertNotIn("remote:odoo", ids)

    def test_ls_remote_on_local_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", "-b", "main", tmp], check=True)
            subprocess.run(["git", "-C", tmp, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"], check=True)
            self.assertEqual(preflight.ls_remote(tmp, "main")[0], "ok")
            self.assertEqual(preflight.ls_remote(tmp, "nope")[0], "missing")
            self.assertEqual(preflight.ls_remote(tmp + "/gone", "main")[0], "auth")


class ServiceTest(ProfileFixture):
    def test_build_flat_and_profile(self):
        spec, resolved = service.build({"version": 17, "dev_user": "alice"})
        self.assertIsNone(resolved)
        self.write("edu", EDU)
        spec, resolved = service.build({"profile": "edu", "destinations": {"admissions": "custom/x/adm"}})
        self.assertEqual(spec.custom[0].destination, "custom/x/adm")
        with self.assertRaisesRegex(service.RequestError, "no repository named"):
            service.build({"profile": "edu", "destinations": {"zzz": "a"}})
        with self.assertRaisesRegex(service.RequestError, "use overrides"):
            service.build({"profile": "edu", "root": "/x"})

    def test_receipt_read_back(self):
        spec = ProvisionSpec(version=17, dev_user="alice", root=str(self.tmp / "missing" / "odoo17"))
        where = execute.write_receipt(spec, "incomplete", "failed in clone", {"completed": ["root-script"]})
        self.assertIn("/state/", where)
        prev = service.read_receipt(spec)
        self.assertEqual((prev["status"], prev["completed"]), ("incomplete", ["root-script"]))
        with mock.patch.object(preflight, "run_preflight", return_value=[]):
            out = service.plan({"version": 17, "dev_user": "alice", "root": spec.root}, remote=False)
        self.assertEqual(out["previous"]["remaining"], ["clone", "pip", "verify"])
        other = ProvisionSpec(version=17, dev_user="alice", root=str(self.tmp / "other"))
        self.assertIsNone(service.read_receipt(other))

    def test_plan_dict(self):
        self.write("edu", EDU)
        with mock.patch.object(preflight, "run_preflight", return_value=[]):
            out = service.plan({"profile": "edu"}, remote=True, remote_check=lambda u, r: ("ok", "found"))
        self.assertTrue(out["ok"])
        self.assertEqual(out["profile"]["origin"]["repos.0"], "profile:edu")
        self.assertIn({"path": "custom/education/admissions", "kind": "custom", "label": "staging-19", "addons": True,
                       "group": None, "name": "admissions"}, out["tree"])
        self.assertTrue(any(c["id"] == "remote:admissions" for c in out["preflight"]))


class ResolvedAddonsTest(unittest.TestCase):
    def test_from_what_was_cloned(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "custom/many/m1").mkdir(parents=True)
            Path(root, "custom/many/m1/__manifest__.py").write_text("{}")
            Path(root, "custom/single").mkdir(parents=True)
            Path(root, "custom/single/__manifest__.py").write_text("{}")
            Path(root, "custom/empty").mkdir(parents=True)
            spec = ProvisionSpec(version=19, dev_user="alice", root=root, custom=[
                CustomRepo("https://h/m.git", "many"), CustomRepo("https://h/s.git", "single"),
                CustomRepo("https://h/e.git", "empty"), CustomRepo("https://h/n.git", "off", destination="custom/off", addons=False)])
            notes = []
            got = execute.resolved_addons_path(spec, notes.append, "config")
            self.assertEqual(got, [f"{root}/odoo/addons", f"{root}/custom/many", f"{root}/custom"])
            self.assertTrue(any("custom/empty" in n["text"] for n in notes))


class BundleTest(GitFixture):
    def setUp(self):
        super().setUp()
        self._cfg = mock.patch.dict(os.environ, {"ODP_CONFIG_DIR": str(self.tmp / "cfg")})
        self._cfg.start()
        (self.tmp / "cfg" / "profiles").mkdir(parents=True)
        (self.tmp / "cfg" / "profiles" / "b.toml").write_text(f"""
[[repos]]
url = "file://{self.remotes}/payroll.git"
destination = "custom/hr/payroll"
[[repos]]
name = "att"
url = "file://{self.remotes}/admissions.git"
destination = "custom/hr/attendance"
group = "hr"
[[repos]]
name = "clash"
url = "file://{self.remotes}/admissions.git"
destination = "custom/busy"
""")
        (self.root / "custom" / "busy").mkdir()
        (self.root / "custom" / "busy" / "f").write_text("x")
        self.conf = self.tmp / "main.conf"
        self.conf.write_text(f"[options]\nadmin_passwd = secret\naddons_path = {self.odoo}/addons,{self.root}/custom/cms/admissions\n")
        self.snap["instances"] = [{"path": str(self.conf), "installation": str(self.root), "options": {}}]
        self.snap["installations"][0]["owner"] = "dev"

    def tearDown(self):
        self._cfg.stop()
        super().tearDown()

    def test_plan_run_then_addons(self):
        p = api.plan("bundle", {"installation": str(self.root), "profile": "b"}, self.snap)
        by = {os.path.basename(i.repo): i for i in p.items}
        self.assertTrue(by["payroll"].keep)
        self.assertIsNone(by["attendance"].skip)
        self.assertIn("not empty", by["busy"].skip)
        self.assertTrue(p.ok)
        result = asyncio.run(ops.run_plan(p, lambda e: None))
        self.assertEqual({k: v for k, v in result["counts"].items() if v}, {"ok": 1, "kept": 1, "skipped": 1})
        assoc = {a["destination"]: a for a in registry.load()["assoc"]}
        self.assertEqual(assoc["custom/hr/attendance"]["group"], "hr")
        self.assertIn("custom/hr/payroll", assoc)
        done = [r["repo"] for r in result["results"] if r["status"] in ("ok", "kept")]
        rows = api.addons_proposal({"installation": str(self.root), "repos": done}, self.snap)
        self.assertEqual(sorted(rows[0]["add"]), sorted(done))
        out = api.addons_apply({"path": str(self.conf), "add": rows[0]["add"], "sha": rows[0]["sha"]}, self.snap)
        self.assertTrue(out["changed"] and os.path.exists(out["backup"]))
        text = self.conf.read_text()
        self.assertIn("custom/hr/attendance", text)
        self.assertIn("admin_passwd = secret", text)
        with self.assertRaisesRegex(api.ApiError, "changed since"):
            api.addons_apply({"path": str(self.conf), "add": rows[0]["add"], "sha": rows[0]["sha"]}, self.snap)

    def test_export_round_trip(self):
        registry.register(str(self.pay), str(self.root), {"group": "hr"})
        (self.root / "custom" / "local").mkdir()
        git(self.root / "custom" / "local", "init", "-q")
        data, notes = export.export_installation(str(self.root), self.snap)
        self.assertEqual(data["odoo_version"], 19)
        repos = {r["destination"]: r for r in data["repos"]}
        self.assertEqual(repos["custom/hr/payroll"]["group"], "hr")
        self.assertEqual(repos["custom/hr/payroll"]["branch"], "staging-18")
        self.assertFalse(repos["custom/hr/payroll"]["addons"])
        self.assertNotIn("addons", repos["custom/cms/admissions"])
        self.assertTrue(any("custom/local" in n for n in notes))
        self.assertIn("odoo_git", data["install"])  # the fixture's community comes from a file:// remote
        text = profiles.dump(data)
        self.assertNotIn("secret", text)
        self.assertEqual(profiles.parse(text, "export"), data)


class ParityTest(ProfileFixture):
    def test_provision_plan_cli_and_rpc(self):
        self.write("edu", EDU)
        side = Sidecar()
        with mock.patch.object(preflight, "run_preflight", return_value=[]), \
                mock.patch.object(preflight, "remote_checks", return_value=[]):
            rpc_out = asyncio.run(side.h_provision_plan({"profile": "edu"}, None))
            import contextlib
            import io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = cli.main(["--json", "provision", "plan", "--profile", "edu"])
        cli_out = json.loads(buf.getvalue())
        self.assertEqual(code, 0)
        for key in ("spec", "tree", "addons_path", "profile", "config"):
            if key == "config":
                continue  # carries the generation time
            self.assertEqual(cli_out[key], rpc_out[key], key)

    def test_rpc_errors(self):
        side = Sidecar()
        with self.assertRaises(rpc.RpcError) as err:
            asyncio.run(side.h_provision_plan({"profile": "missing"}, None))
        self.assertEqual(err.exception.code, rpc.INVALID_PARAMS)
        with self.assertRaises(rpc.RpcError):
            asyncio.run(side.h_profile_save({"name": "x", "data": {"bogus": 1}}, None))


if __name__ == "__main__":
    unittest.main()
