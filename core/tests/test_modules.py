import os
import tempfile
import unittest
from pathlib import Path

from odoo_dev_panel import modules


def make(root: str, name: str, depends=(), **extra) -> None:
    folder = Path(root, name)
    folder.mkdir(parents=True)
    manifest = {"name": name.title(), "depends": list(depends), **extra}
    (folder / "__manifest__.py").write_text(f"# header\n{manifest!r}\n")


class ManifestTest(unittest.TestCase):
    def test_literal_only_and_never_executed(self):
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d, "ran")
            f = Path(d, "__manifest__.py")
            f.write_text(f"__import__('pathlib').Path({str(marker)!r}).touch()\n{{'depends': ['base']}}\n")
            self.assertEqual(modules.read_manifest(str(f)), {"depends": ["base"]})
            self.assertFalse(marker.exists())
            f.write_text("{'depends': [open('/etc/passwd')]}")
            self.assertIsNone(modules.read_manifest(str(f)))
            f.write_text("{'depends': ")
            self.assertIsNone(modules.read_manifest(str(f)))
            f.write_text("x = 1")
            self.assertIsNone(modules.read_manifest(str(f)))


class GraphTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.a = os.path.join(self._tmp.name, "a")
        self.b = os.path.join(self._tmp.name, "b")
        make(self.a, "base")
        make(self.a, "sale", ["base"])
        make(self.a, "sale_x", ["sale"], installable=False)
        make(self.a, "sale_y", ["sale_x", "ghost"])
        make(self.b, "sale", ["base", "other"])  # shadowed by a/sale
        make(self.b, "loop1", ["loop2"])
        make(self.b, "loop2", ["loop1"])
        Path(self.b, "bad").mkdir()
        Path(self.b, "bad", "__manifest__.py").write_text("{")
        self.full = modules.graph(modules.scan([self.a, self.b, "/nope"]))

    def test_scan(self):
        self.assertEqual(self.full["modules"]["sale"]["addons_path"], self.a)
        self.assertEqual([s["name"] for s in self.full["shadowed"]], ["sale"])
        self.assertEqual(len(self.full["unreadable"]), 1)
        self.assertFalse(self.full["modules"]["sale_x"]["installable"])

    def test_required_by_and_missing(self):
        self.assertEqual(self.full["modules"]["sale"]["required_by"], ["sale_x"])
        self.assertEqual(self.full["missing"], {"sale_y": ["ghost"]})

    def test_focus_depth(self):
        f = modules.focus(self.full, "sale", None)
        self.assertEqual(f["needs"], {"base": 1})
        self.assertEqual(f["needed_by"], {"sale_x": 1, "sale_y": 2})
        self.assertEqual(modules.focus(self.full, "sale", 1)["needed_by"], {"sale_x": 1})
        self.assertEqual(modules.focus(self.full, "sale_y")["missing"], {"sale_y": ["ghost"]})
        with self.assertRaises(KeyError):
            modules.focus(self.full, "nope")

    def test_cycles(self):
        found = modules.cycles(self.full)
        self.assertEqual(len(found), 1)
        self.assertEqual(set(found[0]), {"loop1", "loop2"})


if __name__ == "__main__":
    unittest.main()
