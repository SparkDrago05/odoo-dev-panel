import asyncio
import os
import stat
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from odoo_dev_panel import rpc
from odoo_dev_panel.provision import execute
from odoo_dev_panel.provision.spec import CustomRepo, ProvisionSpec

from .helpers import AgentProcess


class ExtractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.dest = self.dir / "enterprise"
        self.dest.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_zip_strips_single_top_folder(self):
        archive = self.dir / "e.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("enterprise-17.0/web_enterprise/__manifest__.py", "{}")
        execute.extract_archive(str(archive), str(self.dest))
        self.assertTrue((self.dest / "web_enterprise" / "__manifest__.py").exists())

    def test_tar_without_top_folder(self):
        src = self.dir / "src"
        (src / "a").mkdir(parents=True)
        (src / "a" / "f.txt").write_text("x")
        (src / "b.txt").write_text("y")
        archive = self.dir / "e.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(src / "a", arcname="a")
            tf.add(src / "b.txt", arcname="b.txt")
        execute.extract_archive(str(archive), str(self.dest))
        self.assertTrue((self.dest / "a" / "f.txt").exists())
        self.assertTrue((self.dest / "b.txt").exists())

    def test_zip_path_escape_refused(self):
        archive = self.dir / "bad.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../evil.txt", "x")
        with self.assertRaises(execute.ProvisionError):
            execute.extract_archive(str(archive), str(self.dest))
        self.assertFalse((self.dir / "evil.txt").exists())

    def test_not_an_archive(self):
        bogus = self.dir / "x.bin"
        bogus.write_text("nope")
        with self.assertRaises(execute.ProvisionError):
            execute.extract_archive(str(bogus), str(self.dest))


class FilesTest(unittest.TestCase):
    def test_private_file_mode_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c.conf")
            execute.write_private(path, "secret")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o640)
            with self.assertRaises(FileExistsError):
                execute.write_private(path, "again")

    def test_only_existing_requirements(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = ProvisionSpec(version=17, dev_user="alice", root=f"{tmp}/odoo17",
                                 custom=[CustomRepo("https://x/a.git", "a"), CustomRepo("https://x/b.git", "b")])
            os.makedirs(f"{tmp}/odoo17/odoo")
            os.makedirs(f"{tmp}/odoo17/custom/b")
            Path(f"{tmp}/odoo17/odoo/requirements.txt").write_text("")
            Path(f"{tmp}/odoo17/custom/b/requirements.txt").write_text("")
            self.assertEqual(execute.existing_requirements(spec),
                             [f"{tmp}/odoo17/odoo/requirements.txt", f"{tmp}/odoo17/custom/b/requirements.txt"])

    def test_pip_overrides_only_for_python_310(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = ProvisionSpec(version=16, dev_user="alice", root=f"{tmp}/odoo16")
            os.makedirs(old.root)
            args = execute.pip_overrides_args(old)
            self.assertEqual(args[0], "--override")
            self.assertIn("gevent==21.12.0", Path(args[1]).read_text())
            new = ProvisionSpec(version=17, dev_user="alice", root=f"{tmp}/odoo17")
            self.assertEqual(execute.pip_overrides_args(new), [])

    def test_receipt_has_no_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = ProvisionSpec(version=17, dev_user="alice", root=f"{tmp}/odoo17")
            os.makedirs(spec.root)
            execute.write_receipt(spec, "incomplete", "clone")
            text = Path(spec.receipt_path).read_text()
            self.assertIn('"incomplete"', text)
            self.assertNotIn("password", text.lower())


class ReuseTest(unittest.TestCase):
    def test_read_conf_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.conf")
            Path(path).write_text("[options]\ndb_user = x\ndb_password = p%w$d\naddons_path = /a,\n\t/b\n")
            self.assertEqual(execute.read_conf_password(path), "p%w$d")
            Path(path).write_text("[options]\ndb_password = False\n")
            self.assertIsNone(execute.read_conf_password(path))
            Path(path).write_text("[options]\nadmin_passwd = x\n")
            self.assertIsNone(execute.read_conf_password(path))
            self.assertIsNone(execute.read_conf_password(os.path.join(tmp, "missing.conf")))

    def test_tree_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "odoo"
            self.assertEqual(execute.tree_state(str(d), "odoo-bin"), "missing")
            d.mkdir()
            self.assertEqual(execute.tree_state(str(d), "odoo-bin"), "missing")
            (d / "random.txt").write_text("x")
            self.assertEqual(execute.tree_state(str(d), "odoo-bin"), "foreign")
            (d / "odoo-bin").write_text("")
            self.assertEqual(execute.tree_state(str(d), "odoo-bin"), "present")

    def test_foreign_tree_is_not_overwritten(self):
        async def go():
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "f").write_text("x")
                fetched = []

                async def fetch():
                    fetched.append(1)
                with self.assertRaises(execute.ProvisionError):
                    await execute.fetch_tree(lambda e: None, "t", tmp, "odoo-bin", fetch, "Odoo")
                self.assertEqual(fetched, [])
        asyncio.run(go())


class RunLocalTest(unittest.IsolatedAsyncioTestCase):
    async def test_output_and_failure(self):
        events = []
        await execute.run_local("t", ["echo", "hi"], events.append)
        self.assertEqual(events, [{"step": "t", "status": "output", "text": "hi"}])
        with self.assertRaises(execute.ProvisionError):
            await execute.run_local("t", ["false"], events.append)

    async def test_preflight_failure_stops_before_root(self):
        called = []

        async def root(script, report):
            called.append(script)
            return 0

        spec = ProvisionSpec(version=17, dev_user="alice", root="/opt/odoo-test-nonexistent-17")
        spec.python_dir = "/nonexistent/python"  # preflight fails: the dev user is not alice
        events = []
        with self.assertRaises(execute.ProvisionError):
            await execute.provision(spec, events.append, root_runner=root)
        self.assertEqual(called, [])
        self.assertEqual(events[-1]["status"], "fail")


class RunInAgentTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "state").mkdir()
        self.agent = AgentProcess(root)
        self.agent.start()
        self.conn = await rpc.open_unix(str(self.agent.socket_path))

    async def asyncTearDown(self):
        await self.conn.close()
        await asyncio.to_thread(self.agent.stop)
        self._tmp.cleanup()

    async def test_streams_output_and_checks_exit_code(self):
        events = []
        await execute.run_in_agent(self.conn, "t", ["/bin/sh", "-c", "echo one; echo two"], "/tmp", events.append)
        self.assertEqual([e["text"] for e in events], ["one", "two"])
        with self.assertRaises(execute.ProvisionError):
            await execute.run_in_agent(self.conn, "t", ["/bin/sh", "-c", "echo bad; exit 3"], "/tmp", events.append)
        self.assertEqual(events[-1]["text"], "bad")


if __name__ == "__main__":
    unittest.main()
