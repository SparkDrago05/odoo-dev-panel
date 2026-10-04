import asyncio
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from odoo_dev_panel import doctor
from odoo_dev_panel.discover import venv as venv_mod
from odoo_dev_panel.doctor import checks, repair, requirements
from odoo_dev_panel.doctor.checks import Context

from .helpers import AgentProcess

REAL_PY = os.path.realpath(sys.executable)
REAL_VERSION = "%d.%d" % sys.version_info[:2]


def fake_interpreter(base: Path, version: str) -> Path:
    """An executable named like a Python, so its version is read from the name."""
    path = base / "interp" / f"python{version}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def make_venv(base: Path, target: Path | str, built: str | None, site: list[str], packages: dict[str, str] | None = None) -> Path:
    venv = base / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").symlink_to(target)
    (venv / "pyvenv.cfg").write_text(f"home = /x\nversion = {built}.3\n" if built else "home = /x\n")
    for v in site:
        sp = venv / "lib" / f"python{v}" / "site-packages"
        sp.mkdir(parents=True)
        for name, version in (packages or {}).items():
            (sp / f"{name}-{version}.dist-info").mkdir()
    return venv


def make_install(base: Path, version: str = "17.0", reqs: str = "", init: str | None = None) -> dict:
    source = base / "odoo"
    (source / "odoo").mkdir(parents=True)
    (source / "odoo-bin").write_text("")
    (source / "requirements.txt").write_text(reqs)
    if init is not None:
        (source / "odoo" / "__init__.py").write_text(init)
    return {"root": str(base), "source": str(source), "version": version, "owner": "odoo17",
            "venv": str(base / "venv"), "home": str(base)}


def snapshot(**parts) -> dict:
    base = {"installations": [], "instances": [], "databases": [], "processes": [], "units": [],
            "ports": {"conflicts": []}, "unreadable": []}
    base.update(parts)
    return base


class Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class VenvInspectTest(Tmp):
    def test_interpreter_upgraded_under_the_venv(self):
        # The reference machine: python -> /usr/bin/python3 -> python3.14, packages in lib/python3.12.
        venv = make_venv(self.base, fake_interpreter(self.base, "3.14"), "3.12", ["3.12"])
        info = venv_mod.inspect(venv)
        self.assertEqual((info.python_version, info.built_for, info.site_versions), ("3.14", "3.12", ["3.12"]))
        self.assertIn("now Python 3.14", info.problem)
        self.assertIn("3.12", info.problem)

    def test_consistent_venv(self):
        info = venv_mod.inspect(make_venv(self.base, fake_interpreter(self.base, "3.12"), "3.12", ["3.12"]))
        self.assertIsNone(info.problem)
        self.assertFalse(info.system_python)

    def test_built_for_differs_without_site_folders(self):
        info = venv_mod.inspect(make_venv(self.base, fake_interpreter(self.base, "3.14"), "3.12", []))
        self.assertIn("built with Python 3.12", info.problem)

    def test_dangling_interpreter(self):
        info = venv_mod.inspect(make_venv(self.base, self.base / "gone" / "python3.12", "3.12", ["3.12"]))
        self.assertIn("missing", info.problem)

    def test_no_python_link(self):
        (self.base / "venv").mkdir()
        self.assertEqual(venv_mod.inspect(self.base / "venv").problem, "bin/python is missing")

    def test_uv_version_info_and_system_python(self):
        venv = make_venv(self.base, REAL_PY, None, [REAL_VERSION])
        (venv / "pyvenv.cfg").write_text(f"home = /x\nversion_info = {REAL_VERSION}\n")
        info = venv_mod.inspect(venv)
        self.assertEqual(info.built_for, REAL_VERSION)
        self.assertIsNone(info.problem)
        self.assertEqual(info.system_python, REAL_PY.startswith("/usr/"))

    def test_installed_names_are_normalized(self):
        sp = self.base / "sp"
        for name in ("Babel-2.10.3.dist-info", "python_dateutil-2.8.2.dist-info", "zope.event-5.0.dist-info",
                     "PyPDF2-1.26.0.egg-info", "PIL", "README"):
            (sp / name).mkdir(parents=True)
        self.assertEqual(venv_mod.installed(sp), {"babel": "2.10.3", "python-dateutil": "2.8.2", "zope-event": "5.0", "pypdf2": "1.26.0"})


class RequirementsTest(unittest.TestCase):
    ODOO = (
        "# comment\n"
        "Babel==2.9.1 ; python_version < '3.11'  # min version\n"
        "Babel==2.10.3 ; python_version >= '3.11' and python_version < '3.13'\n"
        "gevent==21.8.0 ; sys_platform != 'win32' and python_version == '3.10'  # (Jammy)\n"
        "cryptography==42.0.8; python_version >= '3.12'\n"
        "psycopg2==2.9.9\n"
        "pywin32 ; sys_platform == 'win32'\n"
        "requests[security]>=2\n"
        "-r other.txt\n"
        "git+https://example.com/x.git\n"
        "\n"
    )

    def test_parse(self):
        names = [r.name for r in requirements.parse(self.ODOO)]
        self.assertEqual(names, ["babel", "babel", "gevent", "cryptography", "psycopg2", "pywin32", "requests"])

    def test_markers(self):
        env = requirements.environment("3.12")
        ev = lambda m: requirements.evaluate(m, env)  # noqa: E731
        self.assertTrue(ev("python_version >= '3.11' and python_version < '3.13'"))
        self.assertFalse(ev("python_version == '3.10'"))
        self.assertTrue(ev("python_version > '3.9'"))  # versions, not strings: '3.12' > '3.9'
        self.assertTrue(ev("(python_version < '3.10' or sys_platform != 'win32') and os_name == 'posix'"))
        self.assertTrue(ev("'linux' in sys_platform"))
        self.assertTrue(ev("sys_platform not in 'win32 cygwin'"))
        with self.assertRaises(requirements.MarkerError):
            ev("extra == 'test'")
        with self.assertRaises(requirements.MarkerError):
            ev("python_version <")

    def test_applicable_and_missing(self):
        reqs = requirements.parse(self.ODOO)
        apply, unknown = requirements.applicable(reqs, requirements.environment("3.12"))
        self.assertEqual(unknown, [])
        self.assertEqual([r.raw.split(";")[0].strip() for r in apply],
                         ["Babel==2.10.3", "cryptography==42.0.8", "psycopg2==2.9.9", "requests[security]>=2"])
        # psycopg2 is satisfied by the binary wheel
        self.assertEqual(requirements.missing(apply, {"babel": "2.10.3", "psycopg2-binary": "2.9"}), ["cryptography", "requests"])


class PythonRangeTest(Tmp):
    def test_declared_by_the_source(self):
        inst = make_install(self.base, init="MIN_PY_VERSION = (3, 7)\nMAX_PY_VERSION = (3, 12)\n")
        self.assertEqual(checks.python_range(inst["source"], "15.0"), ((3, 7), (3, 12)))

    def test_fallback_for_odoo_14(self):
        inst = make_install(self.base, version="14.0")
        self.assertEqual(checks.python_range(inst["source"], "14.0"), ((3, 6), (3, 10)))
        self.assertEqual(checks.python_range(inst["source"], "13.0"), (None, None))


class VenvCheckTest(Tmp):
    def run_check(self, inst):
        return checks.check_venv(Context(snapshot(installations=[inst]), git=lambda p: ""))

    def test_broken_venv_is_an_error_with_repair(self):
        inst = make_install(self.base, reqs="psycopg2\n")
        make_venv(self.base, fake_interpreter(self.base, "3.14"), "3.12", ["3.12"])
        [f] = self.run_check(inst)  # no H2 on top: every package would look missing
        self.assertEqual((f.check, f.code, f.severity, f.repair), ("H1", "venv-broken", "error", "venv"))

    def test_python_outside_declared_range(self):
        inst = make_install(self.base, version="15.0", init="MIN_PY_VERSION = (3, 7)\nMAX_PY_VERSION = (3, 12)\n")
        make_venv(self.base, fake_interpreter(self.base, "3.14"), "3.14", ["3.14"])
        [f] = self.run_check(inst)
        self.assertEqual(f.code, "venv-python-unsupported")
        self.assertIn("3.7 to 3.12", f.detail)

    def test_odoo_14_gets_no_repair(self):
        inst = make_install(self.base, version="14.0")
        make_venv(self.base, fake_interpreter(self.base, "3.14"), "3.10", ["3.10"])
        [f] = self.run_check(inst)
        self.assertIsNone(f.repair)

    def test_missing_packages(self):
        inst = make_install(self.base, reqs="psycopg2==2.9\nlxml\nzeep ; python_version < '3.0'\n")
        custom = self.base / "custom" / "hr" / "core"
        custom.mkdir(parents=True)
        (custom / "requirements.txt").write_text("pandas\n")
        make_venv(self.base, fake_interpreter(self.base, "3.12"), "3.12", ["3.12"], {"psycopg2_binary": "2.9"})
        found = {f.subject: f for f in self.run_check(inst)}
        core = found[inst["source"] + "/requirements.txt"]
        self.assertEqual((core.check, core.severity, core.detail), ("H2", "error", "lxml"))
        self.assertIn("pip install --python", core.commands[0])
        self.assertEqual(found[str(custom / "requirements.txt")].severity, "warning")

    def test_system_python_warning(self):
        if not REAL_PY.startswith("/usr/"):
            self.skipTest("the test interpreter is not under /usr")
        inst = make_install(self.base)
        make_venv(self.base, REAL_PY, REAL_VERSION, [REAL_VERSION])
        codes = [f.code for f in self.run_check(inst)]
        self.assertIn("venv-system-python", codes)


class ConfigChecksTest(Tmp):
    def test_dead_paths_orphans_and_cross_version(self):
        ok = self.base / "addons"
        ok.mkdir()
        instances = [
            {"path": "/etc/odoo/odoo14/test.conf", "installation": "/opt/odoo14", "problems": [], "version_hint": "14.0",
             "options": {"addons_path": f"{ok},{self.base}/missing"}},
            {"path": "/etc/odoo/odoo13/ulm.conf", "installation": None, "problems": ["no installation found for this config"],
             "version_hint": "13.0", "options": {"addons_path": f"{self.base}/gone"}},
            {"path": "/etc/odoo/odoo15/nust.conf", "installation": "/opt/odoo16", "version_hint": "15.0", "options": {},
             "problems": ["addons_path mixes installations: /opt/odoo15, /opt/odoo16", "config folder says 15.0 but installation is 16.0"]},
        ]
        installs = [{"root": "/opt/odoo15", "version": "15.0"}, {"root": "/opt/odoo16", "version": "16.0"}]
        found = [(f.check, f.code, f.subject) for f in checks.check_configs(Context(snapshot(installations=installs, instances=instances)))]
        self.assertEqual(found, [
            ("H3", "addons-path-missing", "/etc/odoo/odoo14/test.conf"),
            ("H4", "config-orphan", "/etc/odoo/odoo13/ulm.conf"),  # no H3 on top for an orphan
            ("H5", "addons-path-cross-version", "/etc/odoo/odoo15/nust.conf"),
            ("H5", "config-folder-version", "/etc/odoo/odoo15/nust.conf"),
        ])

    def test_secrets_readable_by_everyone(self):
        conf_dir = self.base / "odoo17"
        conf_dir.mkdir()
        exposed, private, no_secret = conf_dir / "a.conf", conf_dir / "b.conf", conf_dir / "c.conf"
        exposed.write_text("[options]\ndb_password = s3cret\n")
        private.write_text("[options]\nadmin_passwd = x\n")
        no_secret.write_text("[options]\ndb_password = False\n")
        exposed.chmod(0o644)
        private.chmod(0o640)
        no_secret.chmod(0o644)
        instances = [{"path": str(p), "installation": "/opt/odoo17"} for p in (exposed, private, no_secret)]
        ctx = Context(snapshot(installations=[{"root": "/opt/odoo17", "owner": "odoo17"}], instances=instances), dev_user="dev")
        [f] = checks.check_secrets(ctx)
        self.assertEqual((f.check, f.subject), ("H10", str(exposed)))
        self.assertIn("0644", f.detail)
        self.assertEqual(f.commands, [f"sudo chown dev:odoo17 {exposed}", f"sudo chmod 0640 {exposed}"])
        self.assertNotIn("s3cret", str(f))


class GitChecksTest(Tmp):
    def test_dead_worktree_and_dubious_ownership(self):
        root = self.base / "odoo17"
        (root / "odoo" / ".git").mkdir(parents=True)
        (root / "enterprise").mkdir()
        (root / "enterprise" / ".git").write_text("gitdir: /home/odoo/.repositories/enterprise/worktrees/17.0\n")
        alive = self.base / "repo.git"
        alive.mkdir()
        (root / "custom" / "hr" / "core").mkdir(parents=True)
        (root / "custom" / "hr" / "core" / ".git").write_text(f"gitdir: {alive}\n")
        (root / "venv" / "src" / "x" / ".git").mkdir(parents=True)  # never entered
        repos = dict(checks.find_repos(str(root)))
        self.assertEqual(set(repos), {str(root / "odoo"), str(root / "enterprise"), str(root / "custom" / "hr" / "core")})

        inst = {"root": str(root), "source": str(root / "odoo")}
        asked = []

        def git(path):
            asked.append(path)
            return "fatal: detected dubious ownership in repository at '...'\n" if path.endswith("/odoo") else ""

        ctx = Context(snapshot(installations=[inst]), uid=os.getuid() + 1, git=git)  # pretend another user owns them
        found = [(f.check, f.subject) for f in checks.check_git(ctx)]
        self.assertIn(("H6", str(root / "enterprise")), found)
        self.assertIn(("H7", str(root / "odoo")), found)
        self.assertNotIn(str(root / "enterprise"), asked)  # a dead worktree is not asked again
        # Repositories you own are never asked.
        ctx = Context(snapshot(installations=[inst]), git=lambda p: self.fail("git called for an owned repo"))
        self.assertEqual([f.check for f in checks.check_git(ctx)], ["H6"])


class RuntimeChecksTest(unittest.TestCase):
    def test_ports_units_filestores(self):
        snap = snapshot(
            installations=[{"root": "/opt/odoo15", "owner": "odoo15"}],
            ports={"conflicts": [{"port": 8069, "kind": "shared", "pids": [10, 11], "holder": 10},
                                 {"port": 8070, "kind": "foreign", "pids": [12], "holder": 99}]},
            units=[{"name": "odoo15_dummy.service", "active_state": "failed", "user": "odoo15", "config": "/etc/odoo/x.conf"},
                   {"name": "ok.service", "active_state": "active"}],
            databases=[{"installation": "/opt/odoo15", "error": None, "databases": [
                {"name": "client_b", "filestore": "/opt/odoo15/.local/share/Odoo/filestore/client_b", "filestore_exists": False},
                {"name": "dummy", "filestore": "/x", "filestore_exists": True},
                {"name": "hidden", "filestore": "/y", "filestore_exists": None}]}],
        )
        ctx = Context(snap)
        found = [(f.check, f.subject) for f in checks.check_runtime(ctx) + checks.check_filestores(ctx)]
        self.assertEqual(found, [("H8", "port 8069"), ("H8", "port 8070"), ("H9", "odoo15_dummy.service"),
                                 ("H11", "/opt/odoo15/.local/share/Odoo/filestore/client_b")])


class DoctorRunTest(unittest.TestCase):
    def test_every_code_is_explained(self):
        source = Path(checks.__file__).read_text()
        used = set(re.findall(r'Finding\(\s*"H\d+", "([a-z-]+)"', source))
        self.assertTrue(used)
        self.assertEqual(used - set(checks.WHY), set())

    def test_a_failing_check_does_not_hide_the_others(self):
        def boom(ctx):
            raise RuntimeError("bad")

        def one(ctx):
            return [checks.Finding("H9", "unit-failed", "error", "u", "t")]

        with mock.patch.object(doctor, "CHECKS", (boom, one)):
            result = doctor.run(context=Context(snapshot()))
        self.assertEqual([f["code"] for f in result["findings"]], ["unit-failed"])
        self.assertEqual(result["findings"][0]["why"], checks.WHY["unit-failed"])
        self.assertTrue(any("boom" in n for n in result["not_checked"]))
        self.assertEqual(result["counts"]["error"], 1)


class RepairPlanTest(Tmp):
    def inst(self, version="17.0"):
        inst = make_install(self.base, version=version, reqs="psycopg2\nlxml\n")
        inst["owner"] = "odoo-test-nobody"
        make_venv(self.base, fake_interpreter(self.base, "3.14"), "3.12", ["3.12"],
                  {"psycopg2": "2.9", "lxml": "5", "pyodbc": "5", "pip": "24", "zope.event": "5"})
        return inst

    def plan(self, inst, processes=(), **kw):
        return repair.plan_venv_repair(inst, list(processes), build_missing=lambda: [], **kw)

    def test_plan_shape(self):
        p = self.plan(self.inst(), agent_running=True)
        self.assertEqual((p.python, p.venv, p.new), ("3.12", str(self.base / "venv"), str(self.base / "venv.new")))
        self.assertEqual(p.extras, ["pyodbc", "zope-event"])  # pip is never carried over
        self.assertEqual([s.id for s in p.steps], ["python", "venv", "pip", "validate", "swap", "verify"])
        self.assertIn("--relocatable", p.steps[1].commands[0])
        self.assertNotIn("pyodbc", p.steps[2].commands[0])

    def test_carry_extras(self):
        p = self.plan(self.inst(), carry_extras=True)
        self.assertIn("pyodbc", p.steps[2].commands[0])

    def test_blocking_checks(self):
        inst = self.inst(version="14.0")
        p = self.plan(inst, processes=[{"pid": 42, "installation": inst["root"], "user": "x"}], agent_running=False)
        failed = {c.id for c in p.checks if c.status == "fail"}
        self.assertTrue({"version", "not-running", "agent", "root-writable"} <= failed, failed)
        self.assertFalse(p.ok)

    def test_leftover_new_venv_is_removed_first(self):
        inst = self.inst()
        (self.base / "venv.new").mkdir()
        p = self.plan(inst)
        self.assertIn("leftover", {c.id for c in p.checks})
        self.assertEqual(p.steps[1].id, "cleanup")


class SwapScriptTest(Tmp):
    def sh(self, script, *args):
        return subprocess.run(["/bin/sh", "-c", script, "x", *args], capture_output=True, text=True).returncode

    def test_swap_and_undo(self):
        venv, new, bak, failed = (str(self.base / n) for n in ("venv", "venv.new", "venv.bak", "venv.failed"))
        os.mkdir(venv)
        Path(venv, "old").write_text("")
        os.mkdir(new)
        Path(new, "new").write_text("")
        self.assertEqual(self.sh(repair.SWAP_SCRIPT, venv, new, bak), 0)
        self.assertTrue(Path(venv, "new").exists() and Path(bak, "old").exists() and not os.path.exists(new))
        self.assertEqual(self.sh(repair.UNDO_SCRIPT, venv, new, bak, failed), 0)
        self.assertTrue(Path(venv, "old").exists() and Path(failed, "new").exists())

    def test_failed_second_move_puts_the_old_venv_back(self):
        venv, bak = str(self.base / "venv"), str(self.base / "venv.bak")
        os.mkdir(venv)
        self.assertNotEqual(self.sh(repair.SWAP_SCRIPT, venv, str(self.base / "missing.new"), bak), 0)
        self.assertTrue(os.path.isdir(venv))
        self.assertFalse(os.path.exists(bak))


FAKE_UV = r"""#!/bin/sh
# Stand-in for uv: "venv" makes a venv whose python is a shell script; "pip" records dist-info folders.
cmd=$1; shift
case "$cmd" in
python) exit 0 ;;
venv)
    for a in "$@"; do dest=$a; done
    mkdir -p "$dest/bin" "$dest/lib/python3.12/site-packages"
    printf 'version_info = 3.12\n' > "$dest/pyvenv.cfg"
    cat > "$dest/bin/python" <<'EOF'
#!/bin/sh
case "$0" in *.new/*) exit 0 ;; esac
root=$(dirname "$(dirname "$(dirname "$0")")")
[ -e "$root/fail-final" ] && { echo "final check failed"; exit 1; }
echo "Odoo Server 17.0"
"""
FAKE_UV_END = r"""EOF
    chmod +x "$dest/bin/python" ;;
pip)
    shift
    py=$2
    site=$(dirname "$(dirname "$py")")/lib/python3.12/site-packages
    while [ $# -gt 0 ]; do
        if [ "$1" = "-r" ]; then
            grep -q FAIL "$2" && { echo "build failed"; exit 1; }
            for n in $(sed 's/[=<>;].*//' "$2"); do mkdir -p "$site/$n-1.0.dist-info"; done
            shift
        fi
        shift
    done ;;
esac
"""


class RepairRunTest(unittest.IsolatedAsyncioTestCase):
    """The whole rebuild through a real agent (running as the current user) and a fake uv."""

    async def asyncSetUp(self):
        import pwd

        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        (tmp / "state").mkdir()
        self.agent = AgentProcess(tmp)
        self.agent.start()
        uv = tmp / "uv"
        uv.write_text(FAKE_UV + FAKE_UV_END)
        uv.chmod(0o755)
        self.root = tmp / "odoo17"
        self.root.mkdir()
        inst = make_install(self.root, reqs="psycopg2\nlxml\n")
        old = make_venv(self.root, fake_interpreter(self.root, "3.14"), "3.12", ["3.12"], {"pyodbc": "5"})
        (old / "marker-old").write_text("")
        self.env = mock.patch.dict(os.environ, {"ODP_SOCKET_DIR": str(self.agent.socket_dir), "ODP_UV": str(uv)})
        self.env.start()
        me = pwd.getpwuid(os.getuid()).pw_name
        self.plan = repair.VenvRepairPlan(
            root=str(self.root), source=inst["source"], version="17.0", run_as=me, python="3.12",
            venv=str(old), new=str(old) + ".new", old_exists=True, requirements=[inst["source"] + "/requirements.txt"],
        )
        self.state = tmp / "receipts"
        self.events = []

    async def asyncTearDown(self):
        self.env.stop()
        await asyncio.to_thread(self.agent.stop)
        self._tmp.cleanup()

    async def run_repair(self):
        return await repair.repair_venv(self.plan, self.events.append, running=lambda p: [], state_dir=self.state)

    def backups(self):
        return sorted(p.name for p in self.root.iterdir() if p.name.startswith("venv."))

    async def test_rebuild_and_swap(self):
        result = await self.run_repair()
        venv = self.root / "venv"
        self.assertTrue((venv / "lib" / "python3.12" / "site-packages" / "lxml-1.0.dist-info").is_dir())
        self.assertFalse((venv / "marker-old").exists())
        self.assertTrue(Path(result["backup"], "marker-old").exists())
        self.assertFalse(os.path.exists(self.plan.new))
        self.assertIn("pyodbc", "\n".join(e["text"] for e in self.events))  # not carried over: reported
        self.assertIn('"complete"', Path(result["receipt"]).read_text())

    async def test_failed_build_leaves_the_old_venv(self):
        Path(self.plan.requirements[0]).write_text("FAIL\n")
        with self.assertRaises(repair.RepairError):
            await self.run_repair()
        self.assertTrue((self.root / "venv" / "marker-old").exists())
        self.assertEqual(self.backups(), [])  # venv.new removed, no backup made
        self.assertEqual(self.events[-1]["status"], "fail")

    async def test_failed_final_check_is_undone(self):
        (self.root / "fail-final").write_text("")
        with self.assertRaises(repair.RepairError):
            await self.run_repair()
        self.assertTrue((self.root / "venv" / "marker-old").exists())
        self.assertEqual([n.split("-")[0] for n in self.backups()], ["venv.failed"])

    async def test_odoo_started_meanwhile_stops_before_the_swap(self):
        with self.assertRaises(repair.RepairError):
            await repair.repair_venv(self.plan, self.events.append, running=lambda p: [4242], state_dir=self.state)
        self.assertTrue((self.root / "venv" / "marker-old").exists())
        self.assertEqual(self.backups(), [])


if __name__ == "__main__":
    unittest.main()
