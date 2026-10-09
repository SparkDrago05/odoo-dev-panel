"""W1-W12: Git workspace on real, disposable repositories (git init in temporary folders, file:// remotes)."""

import asyncio
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from odoo_dev_panel import cli, rpc
from odoo_dev_panel.git import api, discover, explain, ops, registry, runner, state, urls, workspace
from odoo_dev_panel.provision import plan as provision_plan
from odoo_dev_panel.sidecar import Sidecar

FORBIDDEN = ("reset", "clean", "push", "--force", "-f", "rebase", "merge", "--hard", "checkout")


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=runner.env())


def commit(repo, name, text="x"):
    path = Path(repo, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", f"add {name}")


class GitFixture(unittest.TestCase):
    """installation tmp/opt/odoo19 with odoo/ (community), custom/cms/admissions and custom/hr/payroll,
    each cloned from a bare remote in tmp/remotes."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        gitconfig = self.tmp / "gitconfig"
        gitconfig.write_text("[user]\n\tname = t\n\temail = t@example.com\n[init]\n\tdefaultBranch = main\n")
        self._env = mock.patch.dict(os.environ, {
            "GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1",
            "ODP_REPOSITORIES": str(self.tmp / "state" / "repositories.json"),
        })
        self._env.start()
        self.root = self.tmp / "opt" / "odoo19"
        self.remotes = self.tmp / "remotes"
        self.remotes.mkdir()
        self.odoo = self.make_repo("odoo", "odoo", {"odoo-bin": "", "odoo/release.py": "version_info = (19, 0)\n"},
                                   branch="19.0")
        self.adm = self.make_repo("admissions", "custom/cms/admissions", {"adm_core/__manifest__.py": "{}"})
        self.pay = self.make_repo("payroll", "custom/hr/payroll", {"hr_pay/__manifest__.py": "{}"},
                                  branch="staging-18")
        self.snap = {"installations": [{"root": str(self.root), "source": str(self.odoo), "version": "19.0"}],
                     "instances": []}

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def make_repo(self, name, dest, files, branch="main"):
        seed = self.tmp / "seed" / name
        seed.mkdir(parents=True)
        git(seed, "init", "-q", "-b", branch)
        for rel, text in files.items():
            Path(seed, rel).parent.mkdir(parents=True, exist_ok=True)
            Path(seed, rel).write_text(text)
        git(seed, "add", "-A")
        git(seed, "commit", "-qm", "init")
        bare = self.remotes / f"{name}.git"
        git(self.tmp, "clone", "-q", "--bare", str(seed), str(bare))
        git(seed, "remote", "add", "origin", str(bare))
        git(seed, "fetch", "-q", "origin")
        target = self.root / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        git(self.tmp, "clone", "-q", f"file://{bare}", str(target))
        return target

    def push_upstream(self, name, file="new.py"):
        seed = self.tmp / "seed" / name
        commit(seed, file)
        git(seed, "push", "-q", "origin", "HEAD")


class DiscoverTest(GitFixture):
    def test_nested_repos_under_plain_folders(self):
        rows = discover.collect(self.snap, registry.load())
        rel = {r["installations"][0]["relative"]: r["purpose"] for r in rows}
        self.assertEqual(rel, {"odoo": "community", "custom/cms/admissions": "custom", "custom/hr/payroll": "custom"})

    def test_bounded_depth_and_skip(self):
        deep = self.root / "a" / "b" / "c" / "d" / "e"
        deep.mkdir(parents=True)
        git(deep, "init", "-q")
        (self.root / "venv" / "x").mkdir(parents=True)
        git(self.root / "venv" / "x", "init", "-q")
        paths = [p for p, _ in discover.find_repos(str(self.root), discover.MAX_DEPTH)]
        self.assertNotIn(str(deep), paths)
        self.assertFalse(any("/venv/" in p for p in paths))

    def test_addons_path_outside_root_and_non_aarsol_layout(self):
        outside = self.tmp / "work" / "client_x"
        outside.mkdir(parents=True)
        git(outside, "init", "-q")
        commit(outside, "mod/__manifest__.py", "{}")
        snap = {**self.snap, "instances": [{"installation": str(self.root),
                                            "options": {"addons_path": f"{self.odoo}/addons,{outside}"}}]}
        rows = discover.collect(snap, registry.load())
        hit = next(r for r in rows if r["path"] == str(outside))
        self.assertEqual(hit["installations"][0]["relative"], None)
        self.assertEqual(hit["installations"][0]["sources"], ["addons_path"])

    def test_worktree_and_broken_worktree(self):
        wt = self.root / "custom" / "wt"
        git(self.adm, "worktree", "add", "-q", str(wt), "-b", "feature")
        s = state.inspect(str(wt), discover._dot_git(str(wt)))
        self.assertTrue(s.ok and s.worktree)
        self.assertEqual(s.branch, "feature")
        (wt / ".git").write_text("gitdir: /nonexistent/x\n")
        s = state.inspect(str(wt), discover._dot_git(str(wt)))
        self.assertFalse(s.ok)
        self.assertEqual(s.problems[0]["code"], "broken-worktree")


class StateTest(GitFixture):
    def test_clean_tracking(self):
        s = state.inspect(str(self.adm))
        self.assertEqual((s.branch, s.upstream, s.ahead, s.behind, s.dirty), ("main", "origin/main", 0, 0, False))
        self.assertTrue(s.remotes["origin"].startswith("file://"))

    def test_dirty_untracked_and_rename_records(self):
        (self.adm / "adm_core" / "__manifest__.py").write_text("{'x': 1}")
        (self.adm / "1new.txt").write_text("u")
        git(self.pay, "mv", "hr_pay", "2renamed")
        s = state.inspect(str(self.adm))
        self.assertEqual((s.modified, s.untracked, s.staged), (1, 1, 0))
        self.assertEqual({p["code"] for p in s.problems}, {"dirty", "untracked"})
        s = state.inspect(str(self.pay))
        self.assertEqual((s.staged, s.untracked), (1, 0))

    def test_ahead_behind_diverged_detached_shallow(self):
        self.push_upstream("admissions")
        git(self.adm, "fetch", "-q")
        self.assertEqual(state.inspect(str(self.adm)).behind, 1)
        commit(self.adm, "local.py")
        s = state.inspect(str(self.adm))
        self.assertEqual((s.ahead, s.behind), (1, 1))
        self.assertIn("diverged", [p["code"] for p in s.problems])
        git(self.adm, "switch", "-q", "--detach", "HEAD~1")
        self.assertTrue(state.inspect(str(self.adm)).detached)
        shallow = self.root / "custom" / "shallow"
        git(self.tmp, "clone", "-q", "--depth=1", f"file://{self.remotes}/admissions.git", str(shallow))
        self.assertTrue(state.inspect(str(shallow)).shallow)

    def test_no_remote(self):
        local = self.root / "custom" / "local"
        local.mkdir()
        git(local, "init", "-q")
        commit(local, "a.py")
        self.assertIn("no-remote", [p["code"] for p in state.inspect(str(local)).problems])

    def test_foreign_owner_reads_with_safe_directory_for_one_call(self):
        calls = []

        def run(cmd, timeout=10):
            calls.append(cmd)
            return runner.run(cmd, timeout)
        with mock.patch.object(state, "owner_of", return_value=("odoo19", True)):
            s = state.inspect(str(self.adm), run=run)
        self.assertTrue(s.foreign)
        self.assertTrue(all(c[1:3] == ["-c", f"safe.directory={self.adm}"] for c in calls))
        self.assertIn("not-owner", [p["code"] for p in s.problems])
        with self.assertRaises(ValueError):
            runner.argv(str(self.adm), "fetch", foreign=True, read_only=False)

    def test_branch_alignment(self):
        self.assertIsNone(explain.branch_alignment("19.0", "community", "19.0"))
        self.assertEqual(explain.branch_alignment("18.0", "community", "19.0")["code"], "branch-mismatch")
        hint = explain.branch_alignment("staging-18", "custom", "19.0")
        self.assertTrue(hint["heuristic"])
        self.assertIsNone(explain.branch_alignment("main", "custom", "19.0"))
        self.assertEqual(explain.branch_alignment("main", "custom", "19.0", "staging-19")["code"], "branch-mismatch")

    def test_listing_marks_payroll_branch(self):
        rows = {r["name"]: r for r in workspace.listing(self.snap)["repos"]}
        codes = [p["code"] for p in rows["payroll"]["state"]["problems"]]
        self.assertIn("branch-mismatch", codes)
        self.assertNotIn("branch-mismatch", [p["code"] for p in rows["odoo"]["state"]["problems"]])


class UrlTest(unittest.TestCase):
    def test_validate(self):
        for good in ("git@github.com:org/repo.git", "https://github.com/odoo/odoo.git", "ssh://git@host/x.git",
                     "file:///srv/x.git", "/srv/mirror/x.git"):
            self.assertEqual(urls.validate(good), good)
        for bad in ("https://u:p@host/x.git", "https://ghp_token@host/x.git", "-uhost", "ftp://x", "a b", ""):
            with self.assertRaises(urls.UrlError):
                urls.validate(bad)

    def test_redact(self):
        self.assertEqual(urls.redact("https://u:secret@host/x"), "https://***@host/x")
        self.assertEqual(urls.redact("ssh://git@host/x"), "ssh://git@host/x")
        self.assertNotIn("secret", runner.display(["git", "clone", "https://u:secret@h/x", "/d"]))


class PlanTest(GitFixture):
    def all_commands(self, plan):
        return [c for item in plan.items for cmd in item.commands for c in cmd]

    def assert_safe(self, plan):
        words = self.all_commands(plan)
        for bad in FORBIDDEN:
            self.assertNotIn(bad, words, f"{bad} in {words}")

    def test_pull_skips_dirty_and_diverged_and_runs_the_rest(self):
        (self.adm / "adm_core" / "__manifest__.py").write_text("{'x': 1}")
        self.push_upstream("payroll")
        p = ops.plan_pull([(str(self.adm), None), (str(self.pay), None)])
        self.assert_safe(p)
        skip = {i.repo: i.skip for i in p.items}
        self.assertIn("uncommitted", skip[str(self.adm)])
        self.assertIsNone(skip[str(self.pay)])
        self.assertIn("--ff-only", self.all_commands(p))
        result = asyncio.run(ops.run_plan(p, lambda e: None))
        self.assertEqual(result["counts"], {"ok": 1, "kept": 0, "failed": 0, "skipped": 1, "cancelled": 0})
        ok = next(r for r in result["results"] if r["status"] == "ok")
        self.assertEqual((ok["changed_files"], ok["changed_modules"]), (1, []))

    def test_pull_reports_changed_modules(self):
        seed = self.tmp / "seed" / "admissions"
        commit(seed, "adm_core/models.py")
        git(seed, "push", "-q", "origin", "HEAD")
        result = asyncio.run(ops.run_plan(ops.plan_pull([(str(self.adm), None)]), lambda e: None))
        self.assertEqual(result["results"][0]["changed_modules"], ["adm_core"])

    def test_partial_failure_and_cancel_between_repos(self):
        git(self.adm, "remote", "set-url", "origin", str(self.tmp / "gone.git"))
        p = ops.plan_fetch([(str(self.adm), None), (str(self.pay), None), (str(self.odoo), None)])
        calls = []

        def cancelled():
            calls.append(1)
            return len(calls) > 2
        result = asyncio.run(ops.run_plan(p, lambda e: None, cancelled))
        self.assertEqual([r["status"] for r in result["results"]], ["failed", "ok", "cancelled"])
        self.assertEqual(result["results"][0]["problem"]["code"], "remote-missing")

    def test_switch(self):
        seed = self.tmp / "seed" / "admissions"
        git(seed, "switch", "-q", "-c", "staging-19")
        git(seed, "push", "-q", "origin", "staging-19")
        git(self.adm, "fetch", "-q")
        p = ops.plan_switch(str(self.adm), None, "staging-19")
        self.assertEqual(p.items[0].commands[0][-3:], ["switch", "--track", "origin/staging-19"])
        self.assert_safe(p)
        asyncio.run(ops.run_plan(p, lambda e: None))
        self.assertEqual(state.inspect(str(self.adm)).branch, "staging-19")
        (self.adm / "adm_core" / "__manifest__.py").write_text("{'x': 1}")
        self.assertIn("uncommitted", ops.plan_switch(str(self.adm), None, "main").items[0].skip)
        with self.assertRaises(ops.OpError):
            ops.plan_switch(str(self.adm), None, "--orphan")

    def test_switch_in_single_branch_clone_needs_explicit_fetch(self):
        seed = self.tmp / "seed" / "admissions"
        git(seed, "switch", "-q", "-c", "staging-19")
        git(seed, "push", "-q", "origin", "staging-19")
        shallow = self.root / "custom" / "shallow"
        git(self.tmp, "clone", "-q", "--depth=1", "--single-branch", f"file://{self.remotes}/admissions.git", str(shallow))
        self.assertIn("single-branch", ops.plan_switch(str(shallow), None, "staging-19").items[0].skip)
        p = ops.plan_switch(str(shallow), None, "staging-19", fetch_branch=True)
        self.assertEqual(len(p.items[0].commands), 3)
        asyncio.run(ops.run_plan(p, lambda e: None))
        self.assertEqual(state.inspect(str(shallow)).branch, "staging-19")

    def test_checkout_needs_confirmation(self):
        commit(self.adm, "b.py")
        git(self.adm, "tag", "v1")
        p = ops.plan_checkout(str(self.adm), None, "v1", None)
        self.assertFalse(p.ok)
        p = ops.plan_checkout(str(self.adm), None, "v1", "v1")
        self.assertTrue(p.ok)
        self.assertEqual(p.items[0].commands[0][-3:], ["switch", "--detach", "v1"])
        self.assertIn("not a known", ops.plan_checkout(str(self.adm), None, "nope", "nope").items[0].skip)

    def test_foreign_repo_is_never_written(self):
        with mock.patch.object(state, "owner_of", return_value=("odoo19", True)):
            for p in (ops.plan_fetch([(str(self.adm), None)]), ops.plan_pull([(str(self.adm), None)]),
                      ops.plan_switch(str(self.adm), None, "x")):
                self.assertIn("owned by odoo19", p.items[0].skip)
                self.assertFalse(p.ok)


class CloneTest(GitFixture):
    def test_nested_destination_registers_association(self):
        url = f"file://{self.remotes}/payroll.git"
        p = ops.plan_clone(str(self.root), url, "custom/hr/attendance", fields={"group": "hr"})
        self.assertTrue(p.ok, p.checks)
        result = asyncio.run(ops.run_plan(p, lambda e: None))
        self.assertEqual(result["counts"]["ok"], 1)
        target = self.root / "custom" / "hr" / "attendance"
        self.assertTrue((target / ".git").is_dir())
        self.assertEqual(result["results"][0]["addons_path_entry"], str(target))
        assoc = registry.load()["assoc"][0]
        self.assertEqual((assoc["destination"], assoc["group"], assoc["installation"]),
                         ("custom/hr/attendance", "hr", str(self.root)))

    def test_commit_ref_clones_full_then_detaches(self):
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.adm, capture_output=True, text=True).stdout.strip()
        p = ops.plan_clone(str(self.root), f"file://{self.remotes}/admissions.git", "custom/x", sha)
        self.assertNotIn("--depth=1", p.items[0].commands[0])
        asyncio.run(ops.run_plan(p, lambda e: None))
        self.assertTrue(state.inspect(str(self.root / "custom" / "x")).detached)

    def test_destination_refusals(self):
        url = f"file://{self.remotes}/payroll.git"
        (self.root / "custom" / "busy").mkdir()
        (self.root / "custom" / "busy" / "f").write_text("x")
        os.symlink(self.tmp, self.root / "custom" / "escape")
        for dest, text in (("../x", "without .."), ("/abs", "relative"), ("custom/cms/admissions", "already a repository"),
                           ("custom/busy", "not empty"), ("custom/escape/x", "leads outside"), ("", "required"),
                           ("custom/-x", "invalid")):
            p = ops.plan_clone(str(self.root), url, dest)
            self.assertFalse(p.ok, dest)
            self.assertTrue(any(text in c["detail"] for c in p.checks), (dest, p.checks))
        self.assertFalse(os.path.exists(self.tmp / "x"))

    def test_clone_inside_another_repo_warns(self):
        p = ops.plan_clone(str(self.root), f"file://{self.remotes}/payroll.git", "custom/cms/admissions/sub")
        self.assertTrue(p.ok)
        self.assertEqual(next(c for c in p.checks if c["id"] == "nested")["status"], "warn")

    def test_auth_failure_is_explained(self):
        problem = explain.from_stderr("git@github.com: Permission denied (publickey).\nfatal: Could not read")
        self.assertEqual(problem["code"], "auth-failed")
        self.assertEqual(explain.from_stderr("Host key verification failed.")["code"], "host-key")
        self.assertEqual(explain.from_stderr("detected dubious ownership in repository", "/r")["code"],
                         "dubious-ownership")


class RegistryTest(GitFixture):
    def test_one_repo_many_installations(self):
        other = str(self.tmp / "opt" / "odoo18")
        registry.register(str(self.adm), str(self.root), {"purpose": "custom"})
        registry.register(str(self.adm), other, {"bulk": False})
        data = registry.load()
        self.assertEqual(len(data["repos"]), 1)
        self.assertEqual(len(data["assoc"]), 2)
        self.assertEqual(os.stat(registry.path()).st_mode & 0o777, 0o600)
        self.assertTrue(registry.forget(str(self.adm), other))
        self.assertEqual(len(registry.load()["assoc"]), 1)
        self.assertTrue((self.adm / ".git").is_dir())  # forgetting never touches files

    def test_bad_fields(self):
        for fields in ({"purpose": "x"}, {"nope": 1}, {"destination": "../x"}, {"bulk": "yes"}):
            with self.assertRaises(registry.RegistryError):
                registry.register(str(self.adm), str(self.root), fields)

    def test_bulk_selection_respects_flag(self):
        registry.register(str(self.pay), str(self.root), {"bulk": False})
        p = api.plan("fetch", {"bulk": True, "installation": str(self.root)}, self.snap)
        self.assertNotIn(str(self.pay), [i.repo for i in p.items])
        self.assertIn(str(self.adm), [i.repo for i in p.items])

    def test_unknown_repo_is_refused(self):
        stray = self.tmp / "stray"
        stray.mkdir()
        git(stray, "init", "-q")
        with self.assertRaises(api.NotFound):
            api.plan("fetch", {"repos": [str(stray)]}, self.snap)
        api.register_existing({"path": str(stray)}, self.snap)
        self.assertEqual(api.plan("fetch", {"repos": [str(stray)]}, self.snap).items[0].skip, "has no remote")


class ParityTest(GitFixture):
    """The CLI and the sidecar call the same functions and agree on results and errors."""

    def cli_json(self, *argv):
        out = io.StringIO()
        with mock.patch("odoo_dev_panel.discover.scan.scan", return_value=self.snap), contextlib.redirect_stdout(out):
            code = cli.main(list(argv))
        return code, json.loads(out.getvalue()) if out.getvalue().strip() else None

    def sidecar(self, method, params):
        side = Sidecar()

        async def snap():
            return self.snap
        side._snap = snap
        return asyncio.run(side.handlers()[method](params, None))

    def test_list_and_plan_agree(self):
        code, from_cli = self.cli_json("repo", "list", "--json")
        self.assertEqual(code, 0)
        from_rpc = self.sidecar("repo.list", {})
        strip = lambda d: [(r["path"], r["state"]["branch"], r["purpose"]) for r in d["repos"]]  # noqa: E731
        self.assertEqual(strip(from_cli), strip(from_rpc))
        code, plan_cli = self.cli_json("repo", "pull", str(self.adm), "--plan", "--json")
        plan_rpc = self.sidecar("repo.plan", {"op": "pull", "repos": [str(self.adm)]})
        self.assertEqual(plan_cli, plan_rpc)

    def test_errors(self):
        with self.assertRaises(rpc.RpcError) as err:
            self.sidecar("repo.plan", {"op": "fetch", "repos": ["/nonexistent"]})
        self.assertEqual(err.exception.code, rpc.NOT_FOUND)
        with self.assertRaises(rpc.RpcError) as err:
            self.sidecar("repo.plan", {"op": "rm"})
        self.assertEqual(err.exception.code, rpc.INVALID_PARAMS)
        bad = self.sidecar("repo.plan", {"op": "clone", "installation": str(self.root), "url": "https://u:p@h/x",
                                         "destination": "custom/x"})
        self.assertFalse(bad["ok"])
        self.assertNotIn("u:p", json.dumps(bad))
        with mock.patch("odoo_dev_panel.discover.scan.scan", return_value=self.snap), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["repo", "fetch", "/nonexistent", "--plan"]), 2)

    def test_run_refuses_failed_checks(self):
        with self.assertRaises(rpc.RpcError) as err:
            self.sidecar("repo.run", {"op": "clone", "installation": str(self.root), "url": "https://u:p@h/x",
                                      "destination": "custom/x"})
        self.assertEqual(err.exception.code, rpc.CONFLICT)


class IntegrationTest(GitFixture):
    def test_modules_know_their_repository(self):
        from odoo_dev_panel import modules

        full = modules.graph(modules.scan([str(self.adm), str(self.pay)]))
        modules.annotate_repos(full)
        self.assertEqual(full["modules"]["adm_core"]["repo"], str(self.adm))
        self.assertEqual(full["modules"]["hr_pay"]["repo"], str(self.pay))

    def test_provision_registers_cloned_repos(self):
        from odoo_dev_panel.provision import execute
        from odoo_dev_panel.provision.spec import CustomRepo, ProvisionSpec

        spec = ProvisionSpec(version=19, dev_user="dev", root=str(self.root),
                             custom=[CustomRepo(url="git@h:x/admissions.git", name="admissions", branch="staging-19")])
        (self.root / "custom" / "admissions").mkdir()
        git(self.root / "custom" / "admissions", "init", "-q")
        execute.register_repos(spec, lambda e: None, "clone")
        assoc = {a["destination"]: a for a in registry.load()["assoc"]}
        self.assertEqual(assoc["odoo"]["purpose"], "community")
        self.assertEqual(assoc["odoo"]["preferred_branch"], "19.0")
        self.assertEqual(assoc["custom/admissions"]["preferred_branch"], "staging-19")
        rows = {r["path"]: r for r in workspace.listing(self.snap)["repos"]}
        self.assertTrue(rows[str(self.root / "custom" / "admissions")]["registered"])


class ProvisionReuseTest(unittest.TestCase):
    def test_clone_command_unchanged(self):
        self.assertEqual(provision_plan.clone_command("https://h/x.git", "19.0", "/opt/odoo19/odoo"),
                         "git clone --depth=1 --single-branch --no-tags --branch 19.0 https://h/x.git /opt/odoo19/odoo")


if __name__ == "__main__":
    unittest.main()


class DesktopPickTest(unittest.TestCase):
    def test_command_prefers_zenity_then_kdialog(self):
        from odoo_dev_panel import desktop

        z = desktop.command("T", "archive", "/tmp", which=lambda n: n == "zenity" and "/usr/bin/zenity")
        self.assertEqual(z[:2], ["zenity", "--file-selection"])
        self.assertIn("*.tar.gz", " ".join(z))
        k = desktop.command("T", "archive", "/tmp", which=lambda n: n == "kdialog" and "/usr/bin/kdialog")
        self.assertEqual(k[0], "kdialog")
        with self.assertRaises(desktop.PickError):
            desktop.command("T", "any", "/tmp", which=lambda n: None)

    def test_cancel_returns_none(self):
        from odoo_dev_panel import desktop

        with mock.patch.object(desktop, "command", return_value=["false"]):
            self.assertIsNone(asyncio.run(desktop.pick_file("T", "archive")))
        with mock.patch.object(desktop, "command", return_value=["echo", "/x/e.zip"]):
            self.assertEqual(asyncio.run(desktop.pick_file("T", "archive")), "/x/e.zip")
