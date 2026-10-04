import asyncio
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from odoo_dev_panel import dockerops, dockerprov
from odoo_dev_panel.discover import docker as discovery
from tests.test_docker import DB, OFFICIAL, compose, row

FAKE = """#!/bin/sh
echo "docker $*" >> "$ODP_LOG"
case " $* " in
  *" $ODP_FAIL "*) echo boom >&2; exit 2 ;;
esac
exit 0
"""


class Plans(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name

    def plan(self, name="shop2", version="18.0", containers=(), **kw):
        kw.setdefault("listening", {})
        return dockerprov.plan_new(name, version, list(containers), home=self.home, **kw)

    def failed(self, p):
        return {c.id for c in p.checks if c.status == "fail"}

    def test_defaults_and_first_free_port(self):
        p = self.plan()
        self.assertTrue(p.ok, p.checks)
        self.assertEqual((p.port, p.image, p.project), (18069, "odoo:18.0", "odp-shop2"))
        self.assertEqual(p.folder, os.path.join(self.home, "odp-docker", "shop2"))
        self.assertEqual(self.plan(listening={18069: 1, 18070: 2}).port, 18071)
        running = discovery.odoo_containers([OFFICIAL, DB])
        self.assertEqual(self.plan(containers=running, listening={}).port, 18069)

    def test_refusals(self):
        for name in ("", "Bad", "a b", "-x", "../x", "a" * 32):
            self.assertIn("name", self.failed(self.plan(name=name)), name)
        self.assertIn("version", self.failed(self.plan(version="9.0")))
        self.assertIn("port", self.failed(self.plan(port=18069, listening={18069: 1})))
        self.assertIn("port", self.failed(self.plan(port=80)))
        taken = discovery.odoo_containers([OFFICIAL, DB])
        self.assertIn("port", self.failed(self.plan(port=10017, containers=taken)))
        os.makedirs(os.path.join(self.home, "odp-docker", "shop2"))
        self.assertIn("folder", self.failed(self.plan()))
        same = discovery.odoo_containers([row("w", "odoo:17", env=["ODOO_VERSION=17.0"], labels=compose("odp-shop2", "web"))])
        self.assertIn("project", self.failed(self.plan(name="shop2", containers=same)))

    def test_addons_folder(self):
        self.assertIn("addons", self.failed(self.plan(addons="rel")))
        self.assertIn("addons", self.failed(self.plan(addons="/nonexistent/x")))
        self.assertTrue(self.plan(addons=self.home).ok)


class Create(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.log = self.home / "calls.log"
        self.log.write_text("")
        (self.home / "bin").mkdir()
        docker = self.home / "bin" / "docker"
        docker.write_text(FAKE)
        docker.chmod(0o755)

    def run_it(self, coro_fn, fail=""):
        env = {"PATH": f"{self.home / 'bin'}:{os.environ['PATH']}", "ODP_LOG": str(self.log), "ODP_FAIL": fail}
        events = []
        with mock.patch.dict(os.environ, env):
            try:
                return asyncio.run(coro_fn(events.append)), None, events
            except dockerops.DockerError as exc:
                return None, exc, events

    def plan(self, **kw):
        return dockerprov.plan_new("shop2", "18.0", [], home=str(self.home), listening={}, **kw)

    def test_files(self):
        p = self.plan()
        result, err, _ = self.run_it(lambda rep: dockerprov.run_new(p, rep))
        self.assertIsNone(err, err)
        folder = Path(p.folder)
        self.assertEqual(sorted(x.name for x in folder.iterdir()), [".env", ".odp-stack", "addons", "compose.yaml", "config"])
        self.assertEqual(stat.S_IMODE((folder / ".env").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((folder / "config" / "odoo.conf").stat().st_mode), 0o644)
        compose_text = (folder / "compose.yaml").read_text()
        self.assertIn('"127.0.0.1:18069:8069"', compose_text)  # this machine only
        self.assertIn("image: odoo:18.0", compose_text)
        password = (folder / ".env").read_text().strip().split("=", 1)[1]
        self.assertGreaterEqual(len(password), 20)
        self.assertNotIn(password, compose_text)  # the compose file reads it from .env
        self.assertNotIn(password, (folder / "config" / "odoo.conf").read_text())
        calls = self.log.read_text().splitlines()
        self.assertTrue(calls[0].startswith("docker compose -p odp-shop2 --project-directory"))
        self.assertTrue(calls[0].endswith(" pull") and calls[1].endswith(" up -d"))
        self.assertEqual(result["container"], "odp-shop2-web-1")

    def test_own_addons_folder_is_mounted_not_created(self):
        mine = self.home / "my-addons"
        mine.mkdir()
        p = self.plan(addons=str(mine))
        self.run_it(lambda rep: dockerprov.run_new(p, rep))
        self.assertFalse((Path(p.folder) / "addons").exists())
        self.assertIn(f"- {mine}:/mnt/extra-addons", (Path(p.folder) / "compose.yaml").read_text())

    def test_failure_removes_what_the_run_made(self):
        for step in ("pull", "up"):
            self.log.write_text("")
            p = self.plan()
            _result, err, events = self.run_it(lambda rep: dockerprov.run_new(p, rep), fail=step)
            self.assertIsNotNone(err, step)
            self.assertFalse(Path(p.folder).exists(), step)
            self.assertTrue(any(" down -v --remove-orphans" in c for c in self.log.read_text().splitlines()), step)
            self.assertTrue(any(e["step"] == "rollback" for e in events))

    def test_a_failed_plan_runs_nothing(self):
        p = dockerprov.plan_new("Bad", "18.0", [], home=str(self.home), listening={})
        _r, err, _e = self.run_it(lambda rep: dockerprov.run_new(p, rep))
        self.assertIsNotNone(err)
        self.assertEqual(self.log.read_text(), "")
        self.assertFalse((self.home / "odp-docker").exists())


class Delete(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.log = self.home / "calls.log"
        self.log.write_text("")
        (self.home / "bin").mkdir()
        docker = self.home / "bin" / "docker"
        docker.write_text(FAKE)
        docker.chmod(0o755)
        self.folder = self.home / "odp-docker" / "shop2"
        self.folder.mkdir(parents=True)
        (self.folder / dockerprov.MARKER).write_text("{}")
        (self.folder / "compose.yaml").write_text("")
        labels = {**compose("odp-shop2", "web"), "com.docker.compose.project.working_dir": str(self.folder),
                  "com.docker.compose.project.config_files": str(self.folder / "compose.yaml")}
        self.stack = row("odp-shop2-web-1", "odoo:18.0", env=["ODOO_VERSION=18.0"], labels=labels)
        self.mine = discovery.odoo_containers([self.stack])
        self.foreign = discovery.odoo_containers([OFFICIAL, DB])

    def plan(self, containers=None, name="odp-shop2-web-1", confirm=None):
        return dockerprov.plan_delete(name, containers or self.mine, confirm, str(self.home))

    def test_only_stacks_this_app_made(self):
        self.assertEqual(dockerprov.stack_of(self.mine[0], str(self.home)), ("shop2", str(self.folder)))
        self.assertFalse(self.plan(self.foreign, "shop-web-1").ok)
        self.assertIsNone(dockerprov.stack_of(self.foreign[0], str(self.home)))
        (self.folder / dockerprov.MARKER).unlink()
        self.assertFalse(self.plan(confirm="shop2").ok)

    def test_a_project_name_that_does_not_match_its_folder_is_refused(self):
        other = discovery.odoo_containers([row("w", "odoo:18", env=["ODOO_VERSION=18.0"], labels={
            **compose("something-else", "web"), "com.docker.compose.project.working_dir": str(self.folder)})])
        self.assertIsNone(dockerprov.stack_of(other[0], str(self.home)))

    def test_typed_name_needed(self):
        self.assertIn("confirm", {c.id for c in self.plan().checks if c.status == "fail"})
        self.assertIn("confirm", {c.id for c in self.plan(confirm="shop").checks if c.status == "fail"})
        self.assertTrue(self.plan(confirm="shop2").ok)
        with self.assertRaises(dockerops.DockerError):
            self.plan(name="nope")

    def run_it(self, p, fail=""):
        env = {"PATH": f"{self.home / 'bin'}:{os.environ['PATH']}", "ODP_LOG": str(self.log), "ODP_FAIL": fail}
        with mock.patch.dict(os.environ, env):
            try:
                return asyncio.run(dockerprov.run_delete(p, lambda e: None)), None
            except dockerops.DockerError as exc:
                return None, exc

    def test_delete_removes_containers_volumes_and_folder(self):
        result, err = self.run_it(self.plan(confirm="shop2"))
        self.assertIsNone(err, err)
        self.assertFalse(self.folder.exists())
        self.assertIn(" down -v --remove-orphans", self.log.read_text())

    def test_failed_down_keeps_the_folder(self):
        result, err = self.run_it(self.plan(confirm="shop2"), fail="down")
        self.assertIsNotNone(err)
        self.assertTrue(self.folder.exists())

    def test_unconfirmed_plan_runs_nothing(self):
        result, err = self.run_it(self.plan())
        self.assertIsNotNone(err)
        self.assertEqual(self.log.read_text(), "")
        self.assertTrue(self.folder.exists())


if __name__ == "__main__":
    unittest.main()
