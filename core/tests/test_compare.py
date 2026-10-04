import os
import tempfile
import unittest
from pathlib import Path

from odoo_dev_panel import compare


def make_install(root: Path, version: str, packages: dict[str, str], commit: str | None = None) -> dict:
    source = root / "odoo"
    site = root / "venv" / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    (root / "venv" / "bin").mkdir(parents=True)
    (root / "venv" / "bin" / "python").symlink_to("/usr/bin/python3")
    for name, ver in packages.items():
        (site / f"{name}-{ver}.dist-info").mkdir()
    if commit:
        (source / ".git" / "refs" / "heads").mkdir(parents=True)
        (source / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        (source / ".git" / "refs" / "heads" / "main").write_text(commit + "\n")
    return {"root": str(root), "source": str(source), "version": version, "venv": str(root / "venv"),
            "venv_ok": True, "python_version": "3.12"}


class CompareTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        one = make_install(d / "o17", "17.0", {"lxml": "5.0", "psycopg2": "2.9", "only_one": "1"}, "a" * 40)
        two = make_install(d / "o18", "18.0", {"lxml": "5.2", "psycopg2": "2.9", "only_two": "2"}, "b" * 40)
        self.snap = {
            "installations": [one, two],
            "instances": [
                {"path": "/c/a.conf", "name": "a", "installation": one["root"],
                 "options": {"http_port": "8069", "workers": "2", "addons_path": "/x,/y"}},
                {"path": "/c/b.conf", "name": "b", "installation": two["root"],
                 "options": {"http_port": "8070", "limit_time_real": "120", "workers": "2", "addons_path": "/x,/z"}},
                {"path": "/c/orphan.conf", "name": "orphan", "installation": None, "options": {}},
            ],
        }

    def test_diff(self):
        r = compare.compare("/c/a.conf", "/c/b.conf", self.snap)
        facts = {f["key"]: f for f in r["facts"]}
        self.assertEqual((facts["version"]["a"], facts["version"]["b"]), ("17.0", "18.0"))
        self.assertEqual(facts["commit"]["a"], "a" * 12)
        self.assertTrue(facts["python"]["same"])
        self.assertEqual(r["packages"]["only_a"], {"only-one": "1"})
        self.assertEqual(r["packages"]["only_b"], {"only-two": "2"})
        self.assertEqual(r["packages"]["changed"], {"lxml": ["5.0", "5.2"]})
        self.assertEqual(r["options"]["changed"], {"http_port": ["8069", "8070"]})
        self.assertEqual(r["options"]["only_b"], {"limit_time_real": "120"})
        self.assertNotIn("addons_path", r["options"]["changed"])
        self.assertEqual((r["addons"]["only_a"], r["addons"]["only_b"]), (["/y"], ["/z"]))

    def test_same_config_has_no_difference(self):
        r = compare.compare("/c/a.conf", "/c/a.conf", self.snap)
        self.assertTrue(all(f["same"] for f in r["facts"]))
        self.assertEqual(r["packages"], {"only_a": {}, "only_b": {}, "changed": {}})

    def test_orphan_skips_packages(self):
        r = compare.compare("/c/a.conf", "/c/orphan.conf", self.snap)
        self.assertIsNone(r["packages"])
        self.assertTrue(r["b"]["notes"])

    def test_unknown_config(self):
        with self.assertRaises(KeyError):
            compare.describe("/c/none.conf", self.snap)

    def test_commit_from_packed_refs_and_detached_head(self):
        with tempfile.TemporaryDirectory() as d:
            git = Path(d, ".git")
            git.mkdir()
            git.joinpath("HEAD").write_text("ref: refs/heads/main\n")
            git.joinpath("packed-refs").write_text("# pack\n" + "c" * 40 + " refs/heads/main\n")
            self.assertEqual(compare.git_commit(d), "c" * 12)
            git.joinpath("HEAD").write_text("d" * 40 + "\n")
            self.assertEqual(compare.git_commit(d), "d" * 12)
            self.assertIsNone(compare.git_commit(os.path.join(d, "nope")))


if __name__ == "__main__":
    unittest.main()
