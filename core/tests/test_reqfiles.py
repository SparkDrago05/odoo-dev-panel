import os
import tempfile
import unittest
from pathlib import Path

from odoo_dev_panel.doctor import checks, reqfiles
from odoo_dev_panel.pyenv import env


class ReqFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        os.environ["ODP_REQFILES"] = os.path.join(self.root, "state", "requirement-files.json")
        for rel in ("odoo/requirements.txt", "custom/requirements.txt", "custom/a/requirements.txt",
                    "custom/a/b/c/d/requirements.txt", "enterprise/requirements.txt", "venv/lib/requirements.txt",
                    "odoo/addons/x/static/requirements.txt", "custom/a/requirements-dev.txt"):
            path = Path(self.root, rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("marshmallow==3.20.1\n")
        self.inst = {"root": self.root, "source": os.path.join(self.root, "odoo"), "venv": os.path.join(self.root, "venv")}

    def tearDown(self):
        os.environ.pop("ODP_REQFILES", None)
        self.tmp.cleanup()

    def rel(self, files):
        return sorted(os.path.relpath(f, self.root) for f in files)

    def test_default_includes_custom_root_file(self):
        got = self.rel(checks.requirement_files(self.inst))
        self.assertIn("custom/requirements.txt", got)
        self.assertNotIn("enterprise/requirements.txt", got)

    def test_detect_skips_venv_and_static(self):
        got = self.rel(reqfiles.detect(self.root, (self.inst["venv"],)))
        self.assertIn("enterprise/requirements.txt", got)
        self.assertIn("custom/a/requirements-dev.txt", got)
        self.assertNotIn("venv/lib/requirements.txt", got)
        self.assertNotIn("odoo/addons/x/static/requirements.txt", got)

    def test_add_then_used_by_doctor_and_remove(self):
        path = os.path.join(self.root, "enterprise", "requirements.txt")
        reqfiles.add(self.root, path)
        self.assertIn(path, checks.requirement_files(self.inst))
        state = {d["path"]: d["state"] for d in env.detected(self.inst)}
        self.assertEqual(state[path], "added")
        self.assertEqual(state[os.path.join(self.root, "custom", "requirements.txt")], "used")
        self.assertEqual(state[os.path.join(self.root, "custom", "a", "b", "c", "d", "requirements.txt")], "available")
        reqfiles.remove(self.root, path)
        self.assertNotIn(path, checks.requirement_files(self.inst))

    def test_add_refuses_outside_and_wrong_name(self):
        with self.assertRaises(reqfiles.ReqFilesError):
            reqfiles.add(self.root, "/etc/passwd")
        Path(self.root, "notes.txt").write_text("x")
        with self.assertRaises(reqfiles.ReqFilesError):
            reqfiles.add(self.root, os.path.join(self.root, "notes.txt"))


if __name__ == "__main__":
    unittest.main()


class ManifestDepsTest(unittest.TestCase):
    def test_manifest_dependency_missing_row(self):
        from odoo_dev_panel.doctor import manifests
        from odoo_dev_panel.pyenv import env, imports

        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.realpath(tmp)
            addon = Path(root, "custom", "rest", "datamodel")
            addon.mkdir(parents=True)
            (addon / "__manifest__.py").write_text(
                '{"name": "x", "external_dependencies": {"python": ["marshmallow", "marshmallow-objects>=2.0.0", "ldap"]}}')
            site = Path(root, "venv", "lib", "python3.10", "site-packages")
            (site / "marshmallow_objects-2.3.0.dist-info").mkdir(parents=True)
            self.assertEqual(sorted(manifests.external_python(root)), ["ldap", "marshmallow", "marshmallow-objects>=2.0.0"])
            inst = {"root": root, "venv": os.path.join(root, "venv")}
            rows = env._manifest_rows(inst, "3.10", {"marshmallow-objects": "2.3.0"}, set())
            got = {r["name"]: r for r in rows}
            self.assertEqual(sorted(got), ["marshmallow", "python-ldap"])
            self.assertEqual(got["marshmallow"]["status"], "manifest-missing")
        self.assertEqual(imports.missing_in("ModuleNotFoundError: No module named 'marshmallow'"), "marshmallow")
        self.assertEqual(imports.missing_in("ImportError: No module named 'PIL.Image'"), "Pillow")


class ConflictTest(unittest.TestCase):
    def test_custom_pin_wins_over_odoo(self):
        from odoo_dev_panel.doctor import repair, requirements

        with tempfile.TemporaryDirectory() as tmp:
            core, custom, other = (os.path.join(tmp, n) for n in ("core.txt", "custom.txt", "other.txt"))
            Path(core).write_text("python-dateutil==2.7.3\nlxml==4.9.3\n")
            Path(custom).write_text("#python-dateutil==2.7.3\npython-dateutil==2.8.2\nlxml>=4.9\n")
            Path(other).write_text("python-dateutil==2.9.0\n")
            env = requirements.environment("3.10")
            lines, won, left = repair.resolve_conflicts([core, custom], core, env)
            self.assertEqual(lines, ["python-dateutil==2.8.2"])
            self.assertEqual(len(won), 1)
            self.assertEqual(left, [])
            lines, won, left = repair.resolve_conflicts([core, custom, other], core, env)
            self.assertEqual(lines, [])
            self.assertEqual(len(left), 1)
