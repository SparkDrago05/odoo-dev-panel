import asyncio
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from odoo_dev_panel import dockerdb, dockerops
from odoo_dev_panel.discover import docker as discovery
from odoo_dev_panel.doctor import checks
from odoo_dev_panel.doctor.checks import Context

from tests.test_docker import DB, OFFICIAL, PLAIN, bind, row

STAMP = "20261004-120000"


def containers(*rows):
    return discovery.odoo_containers(list(rows))


def stopped(r):
    r = json.loads(json.dumps(r))
    r["State"] = {"Status": "exited", "Running": False, "ExitCode": 0}
    return r


class Actions(unittest.TestCase):
    def setUp(self):
        self.web, self.plain = containers(OFFICIAL, DB, PLAIN)[1], containers(PLAIN)[0]
        self.all = containers(OFFICIAL, DB, PLAIN)

    def plan(self, kind, c=None, **kw):
        return dockerops.plan_action(kind, c or self.web, self.all, listening=kw.pop("listening", {}), **kw)

    def failed(self, p):
        return {c.id for c in p.checks if c.status == "fail"}

    def test_find_accepts_only_odoo_containers(self):
        self.assertEqual(dockerops.find(self.all, "shop-web-1")["name"], "shop-web-1")
        for bad in ("shop-db-1", "nope", "a b", "--rm", "", None):
            with self.assertRaises(dockerops.DockerError):
                dockerops.find(self.all, bad)

    def test_stop_and_start_state(self):
        self.assertTrue(self.plan("stop").ok)
        self.assertEqual(self.plan("stop").steps[0].commands, ["docker stop -t 30 shop-web-1"])
        self.assertIn("state", self.failed(self.plan("start")))
        self.assertIn("state", self.failed(self.plan("stop", self.plain)))
        self.assertTrue(self.plan("start", self.plain).ok)

    def test_start_refuses_a_busy_port(self):
        web = containers(stopped(OFFICIAL), DB)[0]
        p = dockerops.plan_action("start", web, [web], listening={10017: 1})
        self.assertIn("ports", self.failed(p))
        self.assertIn("a process on this machine", p.checks[-1].detail)
        self.assertTrue(dockerops.plan_action("start", web, [web], listening={}).ok)
        other = containers(OFFICIAL, DB)  # a running container publishes the port: not ours
        p = dockerops.plan_action("start", web, [web, *other], listening={})
        self.assertIn("container shop-web-1", p.checks[-1].detail + "container shop-web-1")

    def test_restart_of_a_running_container_ignores_its_own_ports(self):
        self.assertTrue(self.plan("restart", listening={10017: 1}).ok)

    def test_upgrade_runs_through_the_entrypoint(self):
        p = self.plan("upgrade", database="shop", update="sale,crm", install=["hr"])
        self.assertTrue(p.ok, p.checks)
        self.assertEqual(p.mode, "exec")
        self.assertEqual(p.argv, ["docker", "exec", "shop-web-1", "/entrypoint.sh", "odoo", "-d", "shop", "-u", "sale,crm",
                                  "-i", "hr", "--stop-after-init", "--no-http"])
        self.assertNotIn("hunter2", " ".join(p.argv))

    def test_upgrade_refusals(self):
        for kw in ({"database": "x"}, {"update": "a", "database": "bad name"}, {"update": "a;b", "database": "x"},
                   {"update": "a", "database": None}, {"update": "../x", "database": "x"}):
            self.assertFalse(self.plan("upgrade", **kw).ok, kw)
        self.assertFalse(self.plan("upgrade", self.plain, database="x", update="a").ok)  # stopped, not compose

    def test_upgrade_of_a_stopped_compose_service_runs_once(self):
        web = containers(stopped(OFFICIAL), DB)[0]
        p = dockerops.plan_action("upgrade", web, [web], database="shop", update="sale")
        self.assertTrue(p.ok, p.checks)
        self.assertEqual(p.mode, "compose-run")
        self.assertEqual(p.argv[:9], ["docker", "compose", "-p", "shop", "-f", "/home/dev/shop/compose.yaml",
                                      "--project-directory", "/home/dev/shop", "run"])
        self.assertIn("--rm", p.argv)
        self.assertEqual(p.argv[-8:], ["web", "odoo", "-d", "shop", "-u", "sale", "--stop-after-init", "--no-http"])

    def test_unknown_kind_and_serialization(self):
        self.assertFalse(self.plan("rm").ok)
        self.assertEqual(self.plan("stop").as_dict()["container"], "shop-web-1")

    def test_shell_and_logs_argv(self):
        self.assertEqual(dockerops.shell_argv(self.web, "shop")[:4], ["docker", "exec", "-it", "shop-web-1"])
        with self.assertRaises(dockerops.DockerError):
            dockerops.shell_argv(self.plain, "shop")
        with self.assertRaises(dockerops.DockerError):
            dockerops.shell_argv(self.web, "a b")
        self.assertEqual(dockerops.logs_argv("x", 10, True), ["docker", "logs", "--tail", "10", "-f", "x"])
        self.assertEqual(dockerops.logs_argv("x", 10**9)[3], "100000")
        self.assertEqual(dockerops.logs_argv("x", -5)[3], "1")


FAKE_DOCKER = r'''#!/bin/sh
echo "docker $*" >> "$ODP_LOG"
case "$1" in
  exec)
    shift; [ "$1" = -i ] && shift; shift
    tool=$1
    [ "$ODP_FAIL" = "$tool" ] && { echo boom >&2; exit 2; }
    case "$tool" in
      pg_dump) echo dumpdata ;;
      pg_restore) data=$(cat); case "$*" in *--list*) printf '; header\n1; a\n2; b\n' ;; esac ;;
      psql) cat > /dev/null; echo 4 ;;
    esac
    exit 0 ;;
  run)
    [ "$ODP_FAIL" = helper ] && { echo boom >&2; exit 2; }
    shift
    while [ "$1" != "-c" ]; do shift; done
    shift
    script=$1; shift
    exec /bin/sh -c "$script" "$@" ;;
  inspect) echo true ;;
esac
'''


class ContainerDatabases(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)
        self.fs = self.t / "filestore"
        (self.fs / "src").mkdir(parents=True)
        (self.fs / "src" / "a.bin").write_text("x")
        self.log = self.t / "calls.log"
        self.log.write_text("")
        (self.t / "bin").mkdir()
        docker = self.t / "bin" / "docker"
        docker.write_text(FAKE_DOCKER)
        docker.chmod(0o755)
        self.state = self.t / "state"
        self.web = containers(OFFICIAL, DB)[0]
        self.patches = [mock.patch.object(dockerdb, "FS_BASE", str(self.fs))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def ctx(self, stamp=STAMP, sessions=0, extra=(), filestores=("src",), databases=("src",)):
        dbs = [{"name": n, "size": 1000, "filestore": str(self.fs / n),
                "filestore_exists": n in filestores, "filestores": []} for n in (*databases, *extra)]
        return dockerdb.DContext(container=self.web, db_container="shop-db-1", db_running=True, user="odoo", databases=dbs,
                                 filestores=set(filestores) | set(extra), activity={"src": sessions} if sessions else {},
                                 home=str(self.t), stamp=stamp)

    def go(self, kind, fail=None, stamp=STAMP, **kw):
        p = dockerdb.plan_db(kind, self.web, self.ctx(stamp, extra=kw.pop("extra", ())), **kw)
        env = {"PATH": f"{self.t / 'bin'}:{os.environ['PATH']}", "ODP_LOG": str(self.log), "ODP_FAIL": fail or ""}
        with mock.patch.dict(os.environ, env):
            try:
                return p, asyncio.run(dockerdb.run_db(p, lambda e: None, self.state)), None
            except dockerdb.DockerError as exc:
                return p, None, exc

    def calls(self):
        return self.log.read_text().splitlines()

    def failed(self, p):
        return {c.id for c in p.checks if c.status == "fail"}

    def root(self):
        return self.t / "odp-backups" / "docker" / "shop-web-1"

    # plans
    def test_plan_checks(self):
        p = dockerdb.plan_db("clone", self.web, self.ctx(), source="src", target="copy")
        self.assertTrue(p.ok, [c for c in p.checks if c.status == "fail"])
        self.assertEqual(p.filestore_dst, str(self.fs / "copy"))
        self.assertEqual(p.root, "docker:shop-web-1")
        self.assertIn("target-free", self.failed(dockerdb.plan_db("clone", self.web, self.ctx(), source="src", target="src")))
        self.assertIn("source", self.failed(dockerdb.plan_db("backup", self.web, self.ctx(), source="nope")))
        busy = dockerdb.plan_db("drop", self.web, self.ctx(sessions=2), source="src", confirm="src")
        self.assertIn("sessions", self.failed(busy))
        self.assertIn("confirm", self.failed(dockerdb.plan_db("drop", self.web, self.ctx(), source="src")))

    def test_plan_without_a_database_container_or_data_mount(self):
        dead = self.ctx()
        dead.db_running, dead.list_error = False, "the database container shop-db-1 is not running"
        self.assertIn("database-container", self.failed(dockerdb.plan_db("backup", self.web, dead, source="src")))
        no_mount = json.loads(json.dumps(self.web))
        no_mount["data"] = None
        p = dockerdb.plan_db("backup", no_mount, self.ctx(), source="src")
        self.assertTrue(p.ok)
        self.assertIsNone(p.filestore_src)
        self.assertTrue(any(c.id == "filestore" and c.status == "warn" for c in p.checks))

    def test_no_secret_anywhere(self):
        p, result, err = self.go("snapshot", source="src")
        text = json.dumps(p.as_dict()) + "".join(self.calls()) + Path(result["receipt"]).read_text()
        self.assertNotIn("hunter2", text)

    def test_commands_are_exec_and_helper_only(self):
        self.go("clone", source="src", target="copy")
        for line in (c for c in self.calls() if c.startswith("docker")):  # a script argument spans several log lines
            self.assertTrue(line.startswith(("docker exec shop-db-1 ", "docker exec -i shop-db-1 ", "docker run --rm --network none ")), line)
        helper = next(c for c in self.calls() if c.startswith("docker run"))
        self.assertIn("--volumes-from shop-web-1", helper)
        self.assertIn(" sha256:", helper + " ")

    # runs
    def test_snapshot_format_and_prune(self):
        where = self.root() / "snapshots"
        for i in (1, 2, 3):
            d = where / f"src-2026100{i}-000000"
            d.mkdir(parents=True)
            (d / "manifest.json").write_text(json.dumps({"database": "src", "snapshot": True}))
        manual = self.root() / "src-20261001-000001"
        manual.mkdir(parents=True)
        p, result, err = self.go("snapshot", source="src", keep=2)
        self.assertIsNone(err, err)
        folder = Path(result["backup"])
        self.assertEqual(sorted(x.name for x in folder.iterdir()), ["SHA256SUMS", "dump.pgdump", "filestore.tar", "manifest.json"])
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o750)
        self.assertEqual(json.loads((folder / "manifest.json").read_text())["snapshot"], True)
        self.assertEqual(len(result["pruned"]), 2)
        self.assertEqual(sorted(x.name for x in where.iterdir()), ["src-20261003-000000", f"src-{STAMP}"])
        self.assertTrue(manual.exists())
        self.assertEqual([s["name"] for s in dockerdb.list_snapshots("shop-web-1", "src", str(self.t))],
                         [f"src-{STAMP}", "src-20261003-000000"])

    def test_snapshot_failure_removes_partial_folder(self):
        p, result, err = self.go("snapshot", fail="pg_dump", source="src")
        self.assertIsNotNone(err)
        self.assertFalse(Path(p.backup).exists())

    def test_backup_restore_roundtrip_and_tamper(self):
        p, result, err = self.go("backup", source="src")
        self.assertIsNone(err, err)
        folder = result["backup"]
        self.assertTrue(folder.startswith(str(self.root())))
        p, result, err = self.go("restore", target="back", backup=folder)
        self.assertIsNone(err, err)
        self.assertEqual((self.fs / "back" / "a.bin").read_text(), "x")
        Path(folder, "dump.pgdump").write_text("changed")
        self.log.write_text("")
        p, result, err = self.go("restore", target="back2", backup=folder)
        self.assertIn("does not match", str(err))
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.fs / "back2").exists())

    def test_restore_failure_removes_new_database_and_filestore(self):
        p, result, err = self.go("backup", source="src")
        self.log.write_text("")
        p, result, err = self.go("restore", fail="pg_restore", target="back", backup=result["backup"])
        self.assertIsNotNone(err)
        self.assertTrue(any("dropdb" in c and "--if-exists" in c and c.endswith("back") for c in self.calls()))
        self.assertFalse((self.fs / "back").exists())

    def test_clone_copies_filestore_and_never_touches_the_source(self):
        p, result, err = self.go("clone", source="src", target="copy", recipe="default")
        self.assertIsNone(err, err)
        self.assertEqual((self.fs / "copy" / "a.bin").read_text(), "x")
        self.assertTrue((self.fs / "src" / "a.bin").exists())
        psql = [c for c in self.calls() if " psql " in c and "-f -" in c]
        self.assertEqual(len(psql), 1)
        self.assertIn("-d copy", psql[0])
        self.assertFalse(any("dropdb" in c for c in self.calls()))

    def test_clone_filestore_failure_rolls_back(self):
        p, result, err = self.go("clone", fail="helper", source="src", target="copy")
        self.assertIsNotNone(err)
        self.assertFalse((self.fs / "copy").exists())
        self.assertTrue(any("dropdb" in c and c.endswith("copy") for c in self.calls()))

    def test_failed_recipe_keeps_clone_and_says_so(self):
        p, result, err = self.go("clone", fail="psql", source="src", target="copy", recipe="default")
        self.assertIn("NOT neutralized", str(err))
        self.assertTrue((self.fs / "copy").exists())
        self.assertFalse(any("dropdb" in c for c in self.calls()))

    def test_drop_moves_filestore_and_puts_it_back_on_failure(self):
        p, result, err = self.go("drop", source="src", confirm="src")
        self.assertIsNone(err, err)
        self.assertFalse((self.fs / "src").exists())
        self.assertTrue((Path(result["trash"]) / "a.bin").exists())
        self.assertTrue(self.calls()[-1].endswith("dropdb -U odoo -- src"))
        shutil_src = self.fs / "src"
        shutil_src.mkdir()
        (shutil_src / "a.bin").write_text("x")
        p, result, err = self.go("drop", fail="dropdb", source="src", confirm="src")
        self.assertIsNotNone(err)
        self.assertTrue((self.fs / "src" / "a.bin").exists())

    def test_revert_keeps_the_current_database_aside_and_rolls_back(self):
        p, result, err = self.go("snapshot", source="src")
        snap = result["backup"]
        (self.fs / "src" / "a.bin").write_text("changed")
        p, result, err = self.go("revert", fail="pg_restore", stamp="20261005-090000", source="src", backup=snap, confirm="src")
        self.assertIsNotNone(err)
        self.assertEqual((self.fs / "src" / "a.bin").read_text(), "changed")
        self.assertFalse((self.fs / "src_before_20261005_090000").exists())
        calls = self.calls()
        back = next(i for i, c in enumerate(calls) if 'RENAME TO "src"' in c)
        drop = next(i for i, c in enumerate(calls) if "dropdb" in c and "--if-exists" in c)
        self.assertLess(drop, back)
        p, result, err = self.go("revert", stamp="20261005-090000", source="src", backup=snap, confirm="src")
        self.assertIsNone(err, err)
        self.assertEqual((self.fs / "src" / "a.bin").read_text(), "x")
        self.assertEqual((self.fs / "src_before_20261005_090000" / "a.bin").read_text(), "changed")
        self.assertEqual(result["aside"], "src_before_20261005_090000")

    def test_forget_only_snapshots_of_this_container(self):
        p, result, err = self.go("snapshot", source="src")
        snap = Path(result["backup"])
        p, result, err = self.go("forget", backup=str(snap))
        self.assertIsNone(err, err)
        self.assertFalse(snap.exists())
        for bad in (str(self.root()), str(self.root() / "src-20261001-000000"), "/etc", "rel"):
            p = dockerdb.plan_db("forget", self.web, self.ctx(), backup=bad)
            self.assertIn("snapshot", self.failed(p), bad)
        manual = self.root() / "snapshots" / "src-20261001-000000"
        manual.mkdir(parents=True)
        (manual / "manifest.json").write_text(json.dumps({"database": "src"}))
        p, result, err = self.go("forget", backup=str(manual))
        self.assertIn("not a snapshot", str(err))
        self.assertTrue(manual.exists())


class DockerDoctor(unittest.TestCase):
    def run_check(self, rows, listening=(), available=True):
        result = {"available": available, "error": None, "containers": discovery.odoo_containers(rows)}
        ctx = Context({"installations": [], "instances": [], "processes": []}, docker=lambda: result,
                      listening=lambda: {p: 1 for p in listening}, uid_of=lambda c: 100)
        return [(f.check, f.code, f.subject) for f in checks.check_docker(ctx)]

    def test_silent_without_docker(self):
        self.assertEqual(self.run_check([OFFICIAL], available=False), [])

    def test_healthy_stack_has_no_findings_except_missing_host_folders(self):
        found = self.run_check([OFFICIAL, DB])
        self.assertEqual({c for _a, c, _s in found}, {"docker-bind-missing"})  # /home/dev/shop does not exist here

    def test_stopped_container_with_port_in_use_and_exit_code(self):
        r = stopped(OFFICIAL)
        r["State"]["ExitCode"] = 137
        r["State"]["OOMKilled"] = True
        found = self.run_check([r, DB], listening=[10017])
        self.assertIn(("H14", "docker-port-conflict", "shop-web-1:10017"), found)
        self.assertIn(("H14", "docker-exited", "shop-web-1"), found)
        self.assertNotIn(("H14", "docker-port-conflict", "shop-web-1:10017"), self.run_check([OFFICIAL, DB], listening=[10017]))

    def test_database_container_down(self):
        down = stopped(DB)
        self.assertIn(("H14", "docker-db-down", "shop-web-1"), self.run_check([OFFICIAL, down]))
        self.assertNotIn("docker-db-down", {c for _a, c, _s in self.run_check([OFFICIAL, DB])})

    def test_mounts_config_and_addons_on_a_real_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp, "config")
            cfg.mkdir()
            conf = cfg / "odoo.conf"
            conf.write_text("[options]\naddons_path = /mnt/extra-addons,/mnt/extra-addons/gone,/usr/lib/python3/dist-packages/odoo/addons\n")
            conf.chmod(0o600)
            Path(tmp, "addons").mkdir()
            r = row("web", "odoo:17.0", env=["ODOO_VERSION=17.0", "ODOO_RC=/etc/odoo/odoo.conf"],
                    mounts=[bind(str(cfg), "/etc/odoo"), bind(f"{tmp}/addons", "/mnt/extra-addons"),
                            bind(f"{tmp}/data", "/var/lib/odoo", rw=False)])
            found = self.run_check([r])
            codes = {c for _a, c, _s in found}
            self.assertEqual(codes, {"docker-config-permissions", "docker-addons-missing", "docker-bind-missing",
                                     "docker-data-readonly"})
            self.assertIn(("H15", "docker-addons-missing", "web:/mnt/extra-addons/gone"), found)
            conf.chmod(0o644)
            self.assertNotIn("docker-config-permissions", {c for _a, c, _s in self.run_check([r])})

    def test_mounted_source_of_another_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "odoo-bin").write_text("")
            Path(tmp, "odoo").mkdir()
            Path(tmp, "odoo", "release.py").write_text("version_info = (16, 0, 0, FINAL, 0, '')\n")
            r = row("web", "odoo:17.0", env=["ODOO_VERSION=17.0"], mounts=[bind(tmp, "/opt/odoo")])
            self.assertIn(("H15", "docker-version-mismatch", "web:/opt/odoo"), self.run_check([r]))

    def test_every_new_code_is_explained(self):
        source = Path(checks.__file__).read_text()
        import re
        used = set(re.findall(r'Finding\(\s*"H\d+", "([a-z-]+)"', source))
        self.assertTrue({"docker-exited", "docker-db-down", "docker-port-conflict"} <= used)
        self.assertEqual(used - set(checks.WHY), set())
        self.assertEqual({c for c in checks.WHY if c.startswith("docker-")} - used, set())


if __name__ == "__main__":
    unittest.main()
