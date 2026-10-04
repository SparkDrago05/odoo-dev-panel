import asyncio
import json
import os
import pwd
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from odoo_dev_panel.database import commands as cmd, ops, paths, recipes

ME = pwd.getpwuid(os.getuid()).pw_name
CONN = cmd.Conn("db.local", "5433", "odoo17", "s3cret")


def fake_tools(bin_dir: Path, log: Path, fail: str | None = None) -> None:
    """Fake PostgreSQL tools that log argv (never the environment) and can fail on request."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    for tool in ("pg_dump", "pg_restore", "createdb", "dropdb", "psql"):
        script = bin_dir / tool
        script.write_text(
            "#!/bin/sh\n"
            f'echo "{tool} $*" >> {log}\n'
            f'if [ "{fail}" = "{tool}" ]; then echo boom >&2; exit 2; fi\n'
            'if [ "' + tool + '" = pg_dump ]; then\n'
            '  while [ $# -gt 0 ]; do [ "$1" = -f ] && { echo dump > "$2"; }; shift; done\n'
            'fi\n'
            'if [ "' + tool + '" = psql ]; then echo 4; fi\n'
        )
        script.chmod(0o755)


def db_row(name: str, fs: str | None) -> dict:
    return {"name": name, "size": 1000, "filestore": fs, "filestore_exists": bool(fs and os.path.isdir(fs)),
            "filestores": [{"path": fs, "exists": bool(fs and os.path.isdir(fs))}] if fs else []}


class Pure(unittest.TestCase):
    def test_names(self):
        for good in ("client_1", "_x", "A" * 63):
            self.assertIsNone(paths.name_error(good), good)
        for bad in ("", "1abc", "a-b", "a b", "a/b", "../x", "postgres", "Template1", "a'b", "A" * 64):
            self.assertIsNotNone(paths.name_error(bad), bad)

    def test_password_only_in_env(self):
        argvs = [cmd.dump_argv(CONN, "d", "/f"), cmd.restore_argv(CONN, "d", "/f"), cmd.createdb_argv(CONN, "d"),
                 cmd.dropdb_argv(CONN, "d"), cmd.psql_argv(CONN, "d", "-c", "select 1"), cmd.verify_argv(CONN, "d")]
        for argv in argvs:
            self.assertNotIn("s3cret", " ".join(argv))
            self.assertEqual(argv[argv.index("-h") + 1], "db.local")
            self.assertEqual(argv[argv.index("-p") + 1], "5433")
            self.assertEqual(argv[argv.index("-U") + 1], "odoo17")
        self.assertEqual(CONN.env(), {"PGPASSWORD": "s3cret"})
        self.assertEqual(cmd.Conn("h", "1", "u").env(), {})
        self.assertNotIn("s3cret", json.dumps(CONN.public()))

    def test_flags(self):
        self.assertIn("--exit-on-error", cmd.restore_argv(CONN, "d", "/f"))
        self.assertEqual(cmd.createdb_argv(CONN, "d")[-2:], ["--", "d"])
        self.assertIn("--if-exists", cmd.dropdb_argv(CONN, "d", if_exists=True))
        self.assertNotIn("--if-exists", cmd.dropdb_argv(CONN, "d"))

    def test_conn_from_options(self):
        self.assertIsNone(cmd.conn_from_options({"db_user": "False"}))
        c = cmd.conn_from_options({"db_user": "u", "db_host": "False", "db_port": "", "db_password": "False"})
        # Odoo's rules: no db_host is the Unix socket, no db_user is the OS user (the run-as user)
        self.assertEqual((c.host, c.port, c.user, c.password), (None, "5432", "u", None))
        self.assertNotIn("-h", c.args())
        c = cmd.conn_from_options({"db_host": "False", "db_password": "pw"}, "odoo")
        self.assertEqual((c.host, c.user, c.password), ("localhost", "odoo", "pw"), "TCP when a password is set")
        self.assertEqual(cmd.conn_from_options({}, "dev").user, "dev")
        self.assertEqual(cmd.conn_from_options({"db_host": "db.lan"}, "dev").host, "db.lan")

    def test_peer_connection_needs_the_os_user(self):
        socket = cmd.Conn(None, "5432", "odoo")
        self.assertTrue(socket.needs_os_user("dev"))
        self.assertFalse(socket.needs_os_user("odoo"), "own role over the socket: peer works")
        self.assertTrue(cmd.Conn("/var/run/postgresql", "5432", "odoo").needs_os_user("dev"))
        self.assertFalse(cmd.Conn(None, "5432", "odoo", "pw").needs_os_user("dev"))
        self.assertFalse(cmd.Conn("localhost", "5432", "odoo").needs_os_user("dev"))

    def test_recipe_goes_through_stdin(self):
        import subprocess

        out = subprocess.run(["/bin/bash", "-c", cmd.PSQL_STDIN_SCRIPT, "odp", "cat"], capture_output=True, text=True,
                             env={"ODP_SQL": "UPDATE x SET y = '$HOME %s';"}, check=True).stdout
        self.assertEqual(out, "UPDATE x SET y = '$HOME %s';\n")

    def test_clone_script_without_host_uses_the_socket(self):
        import subprocess

        fake = "pg_dump() { echo dump \"$@\"; }; pg_restore() { cat >/dev/null; echo restore \"$@\"; }; "
        out = subprocess.run(["/bin/bash", "-c", fake + cmd.CLONE_SCRIPT, "odp", "", "5432", "u", "a", "b"],
                             capture_output=True, text=True, check=True).stdout
        self.assertNotIn("-h", out)
        self.assertIn("-p 5432 -U u", out)

    def test_paths(self):
        self.assertEqual(paths.filestore_dir("/b", "d"), "/b/d")
        self.assertIsNone(paths.filestore_dir(None, "d"))
        self.assertEqual(paths.backup_dir("/x", "d", "S"), "/x/d-S")
        self.assertEqual(paths.default_dest("/opt/odoo17"), "/opt/odoo17/odp-backups")
        self.assertEqual(paths.trash_dir("/b", "d", "S"), "/b/.trash-d-S")

    def test_tools_resolved_to_absolute_paths(self):
        self.assertTrue(os.path.isabs(ops.resolve(["sh", "-c", "x"])[0]))
        self.assertEqual(ops.resolve(["/bin/sh", "x"]), ["/bin/sh", "x"])
        with self.assertRaises(ops.DbError):
            ops.resolve(["odp-no-such-tool"])

    def test_recipe_render(self):
        sql = recipes.render(recipes.DEFAULT_SQL, "client_1")
        self.assertIn("<> 'client_1'", sql)
        self.assertNotIn("{{database}}", sql)
        with self.assertRaises(ValueError):
            recipes.render("x {{database}}", "a'; drop")
        with self.assertRaises(ValueError):
            recipes.load("../x")

    def test_recipes_dir(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "org.sql").write_text("select '{{database}}';")
            self.assertEqual(recipes.available(Path(t)), ["default", "org"])
            self.assertEqual(recipes.load("org", Path(t)), "select '{{database}}';")


class Plans(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.base = t / "filestore"
        (self.base / "src").mkdir(parents=True)
        (self.base / "src" / "a.bin").write_text("x")
        self.inst = {"root": "/opt/odoo17", "owner": ME, "home": str(t)}
        self.ctx = ops.DbContext(databases=[db_row("src", str(self.base / "src")), db_row("nofs", None)], conn=CONN,
                                 bases=[str(self.base)], processes=[], agent_running=True, stamp="S")

    def plan(self, kind, **kw):
        return ops.plan_db(kind, self.inst, self.ctx, **kw)

    def failed(self, p):
        return {c.id for c in p.checks if c.status == "fail"}

    def test_clone_ok(self):
        p = self.plan("clone", source="src", target="copy")
        self.assertTrue(p.ok, p.checks)
        self.assertEqual(p.filestore_src, str(self.base / "src"))
        self.assertEqual(p.filestore_dst, str(self.base / "copy"))
        self.assertEqual([s.id for s in p.steps], ["createdb", "copy-db", "filestore", "verify"])

    def test_clone_with_recipe_adds_neutralize(self):
        p = self.plan("clone", source="src", target="copy", recipe="default")
        self.assertIn("neutralize", [s.id for s in p.steps])
        self.assertTrue(p.recipe_sum)

    def test_clone_without_filestore_warns(self):
        p = self.plan("clone", source="nofs", target="copy")
        self.assertTrue(p.ok)
        self.assertNotIn("filestore", [s.id for s in p.steps])
        self.assertTrue(any(c.id == "filestore" and c.status == "warn" for c in p.checks))

    def test_clone_refusals(self):
        self.assertIn("target-free", self.failed(self.plan("clone", source="src", target="nofs")))
        self.assertIn("target-name", self.failed(self.plan("clone", source="src", target="bad-name")))
        self.assertIn("source", self.failed(self.plan("clone", source="ghost", target="copy")))
        (self.base / "copy").mkdir()
        self.assertIn("filestore-free", self.failed(self.plan("clone", source="src", target="copy")))

    def test_no_connection_and_agent(self):
        self.ctx.conn = None
        self.assertIn("connection", self.failed(self.plan("clone", source="src", target="c")))
        self.ctx.conn, self.ctx.agent_running = CONN, False
        self.inst["owner"] = "someoneelse"
        self.assertIn("agent", self.failed(self.plan("clone", source="src", target="c")))

    def test_running_odoo_warns_for_clone_blocks_drop(self):
        self.ctx.processes = [{"pid": 7, "installation": "/opt/odoo17", "user": "x"}]
        self.assertTrue(self.plan("clone", source="src", target="c").ok)
        self.assertIn("not-running", self.failed(self.plan("drop", source="src", confirm="src")))

    def test_drop_needs_typed_name(self):
        self.assertIn("confirm", self.failed(self.plan("drop", source="src")))
        self.assertIn("confirm", self.failed(self.plan("drop", source="src", confirm="SRC")))
        self.assertIn("confirm", self.failed(self.plan("drop", source="src", confirm="")))
        p = self.plan("drop", source="src", confirm="src")
        self.assertTrue(p.ok and p.confirmed)
        self.assertEqual([s.id for s in p.steps], ["filestore", "dropdb"])

    def test_drop_blocked_by_sessions(self):
        self.ctx.activity = lambda d: 2
        self.assertIn("sessions", self.failed(self.plan("drop", source="src", confirm="src")))

    def test_neutralize_needs_confirm_and_recipe(self):
        self.assertIn("confirm", self.failed(self.plan("neutralize", source="src")))
        self.assertIn("recipe", self.failed(self.plan("neutralize", source="src", confirm="src", recipe="nope")))
        self.assertTrue(self.plan("neutralize", source="src", confirm="src").ok)

    def test_backup_paths(self):
        p = self.plan("backup", source="src")
        self.assertTrue(p.ok, p.checks)
        self.assertEqual(p.backup, os.path.join(self.inst["home"], "odp-backups", "src-S"))
        self.assertEqual([s.id for s in p.steps], ["mkdir", "dump", "filestore", "manifest", "verify"])
        self.assertIn("dest", self.failed(self.plan("backup", source="src", dest="relative")))

    def test_restore_plan(self):
        p = self.plan("restore", target="back", backup="/x/src-S")
        self.assertTrue(p.ok, p.checks)
        self.assertEqual(p.filestore_dst, str(self.base / "back"))
        self.assertIn("backup", self.failed(self.plan("restore", target="back", backup="rel")))
        self.assertIn("target-free", self.failed(self.plan("restore", target="src", backup="/x/y")))

    def test_serialized_plan_has_no_secret(self):
        p = self.plan("clone", source="src", target="copy")
        self.assertNotIn("s3cret", json.dumps(p.as_dict()))
        self.assertNotIn("s3cret", repr(p))

    def test_server_major_picks_matching_tools(self):
        lib = Path(self.tmp.name) / "pglib"
        (lib / "16" / "bin").mkdir(parents=True)
        (lib / "16" / "bin" / "pg_dump").write_text("")
        self.ctx.server_major, self.ctx.pg_lib = 16, str(lib)
        p = self.plan("clone", source="src", target="copy")
        self.assertEqual(p.pg_bin, str(lib / "16" / "bin"))
        self.ctx.server_major = 15
        p = self.plan("clone", source="src", target="copy")
        self.assertIsNone(p.pg_bin)
        self.assertTrue(any(c.id == "pg-tools" and c.status == "warn" for c in p.checks))

    def test_unknown_kind(self):
        self.assertFalse(self.plan("explode").ok)


class Run(unittest.TestCase):
    """Whole actions with fake PostgreSQL tools, run locally (the run-as user is this user)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)
        self.log = self.t / "calls.log"
        self.log.write_text("")
        self.base = self.t / "filestore"
        (self.base / "src").mkdir(parents=True)
        (self.base / "src" / "a.bin").write_text("x")
        self.state = self.t / "state"
        self.inst = {"root": "/opt/odoo17", "owner": ME, "home": str(self.t)}

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines()

    def run_plan(self, kind, fail=None, stamp="S", **kw):
        fake_tools(self.t / "bin", self.log, fail)
        ctx = ops.DbContext(databases=[db_row("src", str(self.base / "src"))], conn=CONN, bases=[str(self.base)],
                            processes=[], agent_running=True, stamp=stamp)
        p = ops.plan_db(kind, self.inst, ctx, **kw)
        events: list[dict] = []
        env = {"PATH": f"{self.t / 'bin'}:{os.environ['PATH']}"}
        with mock.patch.dict(os.environ, env):
            try:
                result = asyncio.run(ops.run_db(p, events.append, self.state))
                return p, result, events, None
            except ops.DbError as exc:
                return p, None, events, exc

    def test_clone(self):
        p, result, events, err = self.run_plan("clone", source="src", target="copy")
        self.assertIsNone(err)
        self.assertEqual((self.base / "copy" / "a.bin").read_text(), "x")
        self.assertTrue((self.base / "src" / "a.bin").exists())
        calls = self.calls()
        self.assertTrue(calls[0].startswith("createdb") and "copy" in calls[0])
        self.assertTrue(any(c.startswith("pg_dump") for c in calls) and any(c.startswith("pg_restore") for c in calls))
        self.assertTrue(any(c.startswith("psql") and "ir_module_module" in c for c in calls))
        self.assertTrue(Path(result["receipt"]).exists())
        self.assertNotIn("s3cret", Path(result["receipt"]).read_text())
        self.assertNotIn("s3cret", "".join(self.calls()))

    def test_clone_failure_rolls_back_only_the_clone(self):
        p, result, events, err = self.run_plan("clone", fail="pg_restore", source="src", target="copy")
        self.assertIsNotNone(err)
        calls = self.calls()
        self.assertTrue(any(c.startswith("dropdb") and "--if-exists" in c and c.endswith("copy") for c in calls))
        self.assertFalse(any(c.startswith("dropdb") and c.endswith("src") for c in calls))
        self.assertFalse((self.base / "copy").exists())
        self.assertTrue((self.base / "src" / "a.bin").exists())
        receipts = list((self.state / "db").glob("clone-copy-*.json"))
        self.assertEqual(json.loads(receipts[0].read_text())["status"], "failed")

    def test_clone_filestore_failure_removes_new_filestore_and_db(self):
        (self.base / "src" / "unreadable").mkdir()
        (self.base / "src" / "unreadable" / "f").write_text("x")
        os.chmod(self.base / "src" / "unreadable", 0)
        self.addCleanup(os.chmod, self.base / "src" / "unreadable", 0o755)
        if os.access(self.base / "src" / "unreadable", os.R_OK):
            self.skipTest("running as root")
        p, result, events, err = self.run_plan("clone", source="src", target="copy")
        self.assertIsNotNone(err)
        self.assertFalse((self.base / "copy").exists())
        self.assertTrue(any(c.startswith("dropdb") and c.endswith("copy") for c in self.calls()))

    def test_existing_filestore_is_never_removed(self):
        fake_tools(self.t / "bin", self.log)
        ctx = ops.DbContext(databases=[db_row("src", str(self.base / "src"))], conn=CONN, bases=[str(self.base)],
                            processes=[], agent_running=True, stamp="S")
        p = ops.plan_db("clone", self.inst, ctx, source="src", target="copy")
        (self.base / "copy").mkdir()  # appears between plan and run
        (self.base / "copy" / "mine").write_text("keep")
        with mock.patch.dict(os.environ, {"PATH": f"{self.t / 'bin'}:{os.environ['PATH']}"}):
            with self.assertRaises(ops.DbError):
                asyncio.run(ops.run_db(p, lambda e: None, self.state))
        self.assertEqual((self.base / "copy" / "mine").read_text(), "keep")

    def test_failed_plan_runs_nothing(self):
        p, result, events, err = self.run_plan("drop", source="src")
        self.assertIsNotNone(err)
        self.assertEqual(self.calls(), [])
        self.assertTrue((self.base / "src").exists())

    def test_drop_moves_filestore(self):
        p, result, events, err = self.run_plan("drop", source="src", confirm="src")
        self.assertIsNone(err)
        self.assertFalse((self.base / "src").exists())
        trash = Path(result["trash"])
        self.assertTrue((trash / "a.bin").exists())
        self.assertTrue(self.calls()[-1].startswith("dropdb"))

    def test_drop_failure_puts_filestore_back(self):
        p, result, events, err = self.run_plan("drop", fail="dropdb", source="src", confirm="src")
        self.assertIsNotNone(err)
        self.assertTrue((self.base / "src" / "a.bin").exists())
        self.assertEqual([x.name for x in self.base.iterdir()], ["src"])

    def test_backup_and_restore_roundtrip(self):
        p, result, events, err = self.run_plan("backup", source="src")
        self.assertIsNone(err)
        folder = Path(result["backup"])
        self.assertEqual(sorted(x.name for x in folder.iterdir()), ["SHA256SUMS", "dump.pgdump", "filestore.tar", "manifest.json"])
        self.assertEqual(json.loads((folder / "manifest.json").read_text())["database"], "src")
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o750)
        self.assertTrue(any(c.startswith("pg_restore --list") for c in self.calls()))
        self.log.write_text("")
        p, result, events, err = self.run_plan("restore", target="back", backup=str(folder))
        self.assertIsNone(err, err)
        self.assertEqual((self.base / "back" / "a.bin").read_text(), "x")
        self.assertTrue(self.calls()[0].startswith("createdb"))

    def test_restore_refuses_tampered_backup(self):
        p, result, events, err = self.run_plan("backup", source="src")
        folder = Path(result["backup"])
        (folder / "dump.pgdump").write_text("changed")
        self.log.write_text("")
        p, result, events, err = self.run_plan("restore", target="back", backup=str(folder))
        self.assertIsNotNone(err)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.base / "back").exists())

    def test_backup_failure_removes_partial_folder(self):
        p, result, events, err = self.run_plan("backup", fail="pg_dump", source="src")
        self.assertIsNotNone(err)
        self.assertFalse(Path(p.backup).exists())
        self.assertTrue((self.base / "src" / "a.bin").exists())

    def test_neutralize_clone_uses_target_only(self):
        p, result, events, err = self.run_plan("clone", source="src", target="copy", recipe="default")
        self.assertIsNone(err)
        psql = [c for c in self.calls() if c.startswith("psql") and "-f" in c]
        self.assertEqual(len(psql), 1)
        self.assertIn("-d copy", psql[0])
        self.assertNotIn("-d src", psql[0])

    def test_failed_recipe_keeps_clone_and_says_so(self):
        p, result, events, err = self.run_plan("clone", fail="psql", source="src", target="copy", recipe="default")
        self.assertIn("NOT neutralized", str(err))
        self.assertTrue((self.base / "copy").exists())
        self.assertFalse(any(c.startswith("dropdb") for c in self.calls()))
        self.assertTrue((self.base / "src" / "a.bin").exists())


if __name__ == "__main__":
    unittest.main()


STAMP = "20261004-120000"


class Snapshots(unittest.TestCase):
    """Snapshots: backups in their own folder, pruned per database; revert keeps the current database aside."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)
        self.log = self.t / "calls.log"
        self.log.write_text("")
        self.base = self.t / "filestore"
        (self.base / "src").mkdir(parents=True)
        (self.base / "src" / "a.bin").write_text("x")
        self.state = self.t / "state"
        self.inst = {"root": "/opt/odoo17", "owner": ME, "home": str(self.t)}
        self.snaps = self.t / "odp-backups" / "snapshots"

    def ctx(self, stamp=STAMP, processes=(), extra=()):
        return ops.DbContext(databases=[db_row("src", str(self.base / "src")), db_row("bad-name", None), *extra],
                             conn=CONN, bases=[str(self.base)], processes=list(processes), agent_running=True, stamp=stamp)

    def go(self, kind, fail=None, stamp=STAMP, **kw):
        fake_tools(self.t / "bin", self.log, fail)
        p = ops.plan_db(kind, self.inst, self.ctx(stamp), **kw)
        with mock.patch.dict(os.environ, {"PATH": f"{self.t / 'bin'}:{os.environ['PATH']}"}):
            try:
                return p, asyncio.run(ops.run_db(p, lambda e: None, self.state)), None
            except ops.DbError as exc:
                return p, None, exc

    def calls(self):
        return self.log.read_text().splitlines()

    def failed(self, p):
        return {c.id for c in p.checks if c.status == "fail"}

    def fake_snapshot(self, name, snapshot=True, database="src"):
        d = self.snaps / name
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({"database": database, **({"snapshot": True} if snapshot else {})}, indent=2))
        return d

    def test_pure(self):
        self.assertTrue(paths.is_snapshot_of(f"/x/src-{STAMP}", "src"))
        self.assertTrue(paths.is_snapshot_of(f"/x/src-{STAMP}/", "src"))
        self.assertFalse(paths.is_snapshot_of(f"/x/src2-{STAMP}", "src"))
        self.assertFalse(paths.is_snapshot_of("/x/src-S", "src"))
        self.assertEqual(paths.aside_name("src", STAMP), "src_before_20261004_120000")
        long = paths.aside_name("a" * 63, STAMP)
        self.assertEqual(len(long), 63)
        self.assertIsNone(paths.name_error(long))
        self.assertEqual(paths.snapshot_root("/home/u"), "/home/u/odp-backups/snapshots")

    def test_parse_snapshots(self):
        lines = ["ODP:snap\tsrc-20261004-120000\t2048", json.dumps({"database": "src", "snapshot": True, "filestore": True}),
                 "ODP:snap\tsrc-20261004-130000\t10", json.dumps({"database": "src", "snapshot": True}),
                 "ODP:snap\tmanual\t5", json.dumps({"database": "src"}),
                 "ODP:snap\tother-20261004-120000\t5", json.dumps({"database": "other", "snapshot": True}),
                 "ODP:snap\tbroken\t5", "{not json"]
        got = ops.parse_snapshots("/s", lines)
        self.assertEqual([g["name"] for g in got], ["src-20261004-130000", "src-20261004-120000", "other-20261004-120000"])
        self.assertEqual(got[1]["bytes"], 2048)
        self.assertEqual(got[1]["path"], "/s/src-20261004-120000")
        self.assertEqual(len(ops.parse_snapshots("/s", lines, "src")), 2)

    def test_snapshot_plan(self):
        p = ops.plan_db("snapshot", self.inst, self.ctx(), source="src", dest="/elsewhere")
        self.assertTrue(p.ok, p.checks)
        self.assertEqual(p.backup, str(self.snaps / f"src-{STAMP}"))
        self.assertEqual(p.keep, ops.DEFAULT_KEEP)
        self.assertEqual(p.steps[-1].id, "prune")
        self.assertIn("source-name", self.failed(ops.plan_db("snapshot", self.inst, self.ctx(), source="bad-name")))
        self.assertIn("keep", self.failed(ops.plan_db("snapshot", self.inst, self.ctx(), source="src", keep=0)))

    def test_revert_plan(self):
        snap = str(self.snaps / f"src-{STAMP}")
        p = ops.plan_db("revert", self.inst, self.ctx(stamp="20261005-090000"), source="src", backup=snap, confirm="src")
        self.assertTrue(p.ok, [c for c in p.checks if c.status == "fail"])
        self.assertEqual((p.target, p.aside), ("src", "src_before_20261005_090000"))
        self.assertEqual(p.filestore_dst, str(self.base / "src"))
        self.assertEqual(p.aside_fs, str(self.base / "src_before_20261005_090000"))
        self.assertEqual([s.id for s in p.steps], ["checksums", "rename", "aside-fs", "createdb", "restore", "filestore", "verify"])
        self.assertIn("confirm", self.failed(ops.plan_db("revert", self.inst, self.ctx(), source="src", backup=snap)))
        self.assertIn("snapshot", self.failed(ops.plan_db("revert", self.inst, self.ctx(), source="src", confirm="src",
                                                          backup=str(self.snaps / f"other-{STAMP}"))))
        running = self.ctx(processes=[{"pid": 7, "installation": "/opt/odoo17", "user": ME}])
        self.assertIn("not-running", self.failed(ops.plan_db("revert", self.inst, running, source="src", backup=snap, confirm="src")))
        taken = self.ctx(extra=[db_row(paths.aside_name("src", STAMP), None)])
        self.assertIn("aside-free", self.failed(ops.plan_db("revert", self.inst, taken, source="src", backup=snap, confirm="src")))

    def test_forget_plan(self):
        self.assertTrue(ops.plan_db("forget", self.inst, self.ctx(), backup=str(self.snaps / f"src-{STAMP}")).ok)
        for bad in (str(self.t / "odp-backups" / f"src-{STAMP}"), str(self.snaps / "manual"), str(self.snaps), "rel/x", None):
            self.assertIn("snapshot", self.failed(ops.plan_db("forget", self.inst, self.ctx(), backup=bad)), bad)

    def test_snapshot_prunes_only_old_snapshots_of_the_same_database(self):
        old = [self.fake_snapshot(f"src-2026100{i}-000000") for i in (1, 2, 3)]
        manual = self.fake_snapshot("src-20261001-000001", snapshot=False)
        other = self.fake_snapshot("other-20261001-000000", database="other")
        p, result, err = self.go("snapshot", source="src", keep=2)
        self.assertIsNone(err, err)
        folder = Path(result["backup"])
        self.assertTrue(json.loads((folder / "manifest.json").read_text())["snapshot"])
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o750)
        self.assertEqual(sorted(Path(x).name for x in result["pruned"]), [o.name for o in old[:2]])
        self.assertTrue(old[2].exists() and manual.exists() and other.exists())
        self.assertFalse(old[0].exists() or old[1].exists())

    def test_list_snapshots_locally(self):
        self.fake_snapshot(f"src-{STAMP}")
        self.fake_snapshot("manual", snapshot=False)
        got = asyncio.run(ops.list_snapshots(self.inst, self.ctx()))
        self.assertEqual([g["name"] for g in got], [f"src-{STAMP}"])
        self.assertEqual(asyncio.run(ops.list_snapshots({**self.inst, "home": str(self.t / "none")}, self.ctx())), [])

    def test_revert_keeps_current_database_aside(self):
        p, result, err = self.go("snapshot", source="src")
        snap = result["backup"]
        (self.base / "src" / "a.bin").write_text("changed after the snapshot")
        self.log.write_text("")
        p, result, err = self.go("revert", stamp="20261005-090000", source="src", backup=snap, confirm="src")
        self.assertIsNone(err, err)
        aside = "src_before_20261005_090000"
        self.assertEqual((self.base / "src" / "a.bin").read_text(), "x")
        self.assertEqual((self.base / aside / "a.bin").read_text(), "changed after the snapshot")
        calls = self.calls()
        rename = next(i for i, c in enumerate(calls) if "ALTER DATABASE" in c)
        create = next(i for i, c in enumerate(calls) if c.startswith("createdb"))
        self.assertLess(rename, create)
        self.assertIn(f'RENAME TO "{aside}"', calls[rename])
        self.assertEqual(result["aside"], aside)
        self.assertFalse(any(c.startswith("dropdb") for c in calls))

    def test_revert_failure_puts_everything_back(self):
        p, result, err = self.go("snapshot", source="src")
        snap = result["backup"]
        (self.base / "src" / "a.bin").write_text("current")
        self.log.write_text("")
        p, result, err = self.go("revert", fail="pg_restore", stamp="20261005-090000", source="src", backup=snap, confirm="src")
        self.assertIsNotNone(err)
        self.assertEqual((self.base / "src" / "a.bin").read_text(), "current")
        self.assertFalse((self.base / "src_before_20261005_090000").exists())
        calls = self.calls()
        drop = next(i for i, c in enumerate(calls) if c.startswith("dropdb"))
        back = next(i for i, c in enumerate(calls) if 'RENAME TO "src"' in c)
        self.assertIn("--if-exists", calls[drop])
        self.assertLess(drop, back)

    def test_forget_removes_only_snapshots(self):
        snap = self.fake_snapshot(f"src-{STAMP}")
        p, result, err = self.go("forget", backup=str(snap))
        self.assertIsNone(err, err)
        self.assertFalse(snap.exists())
        fake = self.fake_snapshot("src-20261001-000000", snapshot=False)
        p, result, err = self.go("forget", backup=str(fake))
        self.assertIsNotNone(err)
        self.assertTrue(fake.exists())
