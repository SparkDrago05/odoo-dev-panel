"""``odp`` command line. Every GUI action has a CLI equivalent."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from . import __version__, client, paths, privilege, rpc
from .discover.ports import listening_ports


def _print(data, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2))


def _sessions_table(sessions: list[dict]) -> None:
    if not sessions:
        print("no sessions")
        return
    print(f"{'ID':12}  {'USER':10}  {'STATE':8}  {'PID':>7}  {'STARTED':25}  NAME")
    for s in sessions:
        print(f"{s['id']:12}  {s['user']:10}  {s['state']:8}  {s['pid']:>7}  {s['started_at']:25}  {s['name']}")


async def _with_agent(user: str, func):
    conn = await client.connect(user)
    try:
        return await func(conn)
    finally:
        await conn.close()


async def cmd_agent_status(args) -> int:
    users = [args.user] if args.user else client.candidate_users()
    statuses = [await client.agent_status(u) for u in users]
    if args.json:
        _print(statuses, True)
        return 0
    if not statuses:
        print(f"no agents found in {paths.socket_dir()}")
    for status in statuses:
        if status["state"] == "running":
            info = status["info"]
            print(f"{status['user']:10}  running  pid {info['pid']}  sessions {info['running_sessions']}  since {info['started_at']}")
        else:
            print(f"{status['user']:10}  {status['state']}  ({status.get('error', '')})")
    return 0


async def cmd_agent_stop(args) -> int:
    result = await _with_agent(args.user, lambda c: c.request("agent.shutdown"))
    print(f"agent for {args.user} stopping; {result['running_sessions']} session(s) keep running")
    return 0


async def cmd_run(args) -> int:
    argv = args.argv[1:] if args.argv and args.argv[0] == "--" else args.argv
    session = await _with_agent(
        args.user, lambda c: c.request("session.start", {"argv": argv, "cwd": args.cwd, "name": args.name})
    )
    if args.json:
        _print(session, True)
    else:
        print(f"started session {session['id']} (pid {session['pid']}) as {session['user']}")
    return 0


async def cmd_ps(args) -> int:
    sessions = []
    users = [args.user] if args.user else list(client.agent_sockets())
    for user in users:
        try:
            sessions += await _with_agent(user, lambda c: c.request("session.list"))
        except rpc.RpcError as exc:
            print(f"{user}: {exc.message}", file=sys.stderr)
    if args.json:
        _print(sessions, True)
    else:
        _sessions_table(sessions)
    return 0


async def cmd_stop(args) -> int:
    session = await _with_agent(
        args.user, lambda c: c.request("session.stop", {"id": args.id, "timeout": args.timeout}, timeout=args.timeout + 10)
    )
    if args.json:
        _print(session, True)
    else:
        print(f"session {session['id']}: {session['state']} (exit code {session['exit_code']})")
    return 0


async def cmd_logs(args) -> int:
    done = asyncio.Event()

    async def on_output(params, _conn):
        sys.stdout.write(params["data"])
        sys.stdout.flush()

    async def on_ended(params, _conn):
        done.set()

    if not args.follow:
        async def read_all(conn):
            offset = 0
            while True:
                chunk = await conn.request("session.read", {"id": args.id, "offset": offset})
                if not chunk["data"]:
                    return
                sys.stdout.write(chunk["data"])
                offset = chunk["offset"]

        await _with_agent(args.user, read_all)
        return 0

    conn = await client.connect(args.user, {"session.output": on_output, "session.ended": on_ended})
    try:
        await conn.request("session.follow", {"id": args.id, "offset": 0})
        closed = asyncio.ensure_future(conn.closed.wait())
        ended = asyncio.ensure_future(done.wait())
        await asyncio.wait({closed, ended}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        await conn.close()
    return 0


def _spec_from_args(args):
    from .provision.spec import CustomRepo, ProvisionSpec, SpecError

    try:
        return ProvisionSpec(
            version=args.odoo_version,
            **{k: v for k, v in {
                "dev_user": args.dev_user, "run_as": args.run_as, "root": args.root, "python": args.python,
                "odoo_git": args.odoo_git, "odoo_branch": args.odoo_branch,
                "enterprise_git": args.enterprise_git, "enterprise_branch": args.enterprise_branch,
                "enterprise_archive": args.enterprise_archive, "http_port": args.http_port,
            }.items() if v},
            custom=[CustomRepo.parse(c) for c in args.custom],
            config_name=args.config_name, pg_host=args.pg_host, pg_port=args.pg_port,
        )
    except SpecError as exc:
        print(f"odp: {exc}", file=sys.stderr)
        return None


def cmd_provision_plan(args) -> int:
    from .provision import plan as plan_mod
    from .provision import preflight

    spec = _spec_from_args(args)
    if spec is None:
        return 2

    secrets_ = plan_mod.Secrets.generate()
    script = plan_mod.render_root_script(spec, secrets_)
    if args.script_only:
        print(script, end="")
        return 0
    checks = preflight.run_preflight(spec)
    steps = plan_mod.build_steps(spec, secrets_)
    placeholder = plan_mod.Secrets("<generated at run time>", "<generated at run time>")
    conf = plan_mod.render_conf(spec, placeholder)

    if args.json:
        from dataclasses import asdict

        _print({"spec": asdict(spec), "preflight": [asdict(c) for c in checks],
                "steps": [asdict(s) for s in steps], "root_script": script, "config": conf}, True)
    else:
        print(f"Provision Odoo {spec.version} as {spec.run_as} in {spec.root}  (dry run: nothing is changed)\n")
        print("Preflight")
        for c in checks:
            print(f"  {c.status.upper():4}  {c.id:18} {c.detail}")
        print("\nSteps")
        for s in steps:
            print(f"  {s.phase}. [{s.actor:5}] {s.title}")
            for command in s.commands:
                print(f"       $ {command}")
        print(f"\nConfig {spec.conf_path}\n" + "".join(f"  | {line}\n" for line in conf.splitlines()))
        print("Root script (run with: odp provision plan ... --script-only)\n")
        print("".join(f"  | {line}\n" for line in script.splitlines()))
    return 1 if preflight.has_failures(checks) else 0


async def cmd_provision_run(args) -> int:
    from .provision import execute

    spec = _spec_from_args(args)
    if spec is None:
        return 2
    if not args.yes:
        print(f"Provision Odoo {spec.version} as {spec.run_as} in {spec.root}. Review first: odp provision plan ...")
        if input("Run it now? sudo will ask for your password once. [y/N] ").strip().lower() != "y":
            print("cancelled")
            return 1

    def report(event: dict) -> None:
        if event["status"] == "output":
            print(f"    {event['text']}", flush=True)
        elif event["status"] == "start":
            print(f"== {event['step']}: {event['text']}", flush=True)
        elif event["status"] == "ok":
            print(f"== {event['step']}: ok", flush=True)
        else:
            print(f"== {event['step']}: FAILED {event['text']}", file=sys.stderr, flush=True)

    try:
        await execute.provision(spec, report)
    except execute.ProvisionError as exc:
        print(f"odp: provision failed: {exc}", file=sys.stderr)
        print(f"Receipt (if created): {spec.receipt_path}. Nothing is rolled back; run it again to reuse what exists and continue.", file=sys.stderr)
        return 1
    print(f"Odoo {spec.version} is ready in {spec.root}. Config: {spec.conf_path}")
    return 0


async def cmd_start(args) -> int:
    from . import run
    from .discover import scan

    snap = scan.scan(with_databases=False)
    params = {
        "db": args.db, "http_port": args.port, "update": args.update, "install": args.install,
        "stop_after_init": args.stop_after_init, "dev": args.dev, "extra": args.extra, "shell": args.shell,
    }
    planned = run.plan(snap, args.instance, params, set(listening_ports()))
    user = planned.pop("user")
    if args.dry_run:
        print(f"as {user}: {' '.join(planned['argv'])}")
        return 0
    if args.shell:
        return await _attach_shell(user, planned)
    session = await _with_agent(user, lambda c: c.request("session.start", planned))
    if args.json:
        _print(session, True)
    else:
        port = session["meta"].get("port")
        print(f"started session {session['id']} (pid {session['pid']}) as {user}" + (f", http://localhost:{port}" if port else ""))
    return 0


async def _attach_shell(user: str, planned: dict) -> int:
    """Line-mode attach: stdin lines go to the PTY, its output comes back through follow. Ctrl-D ends the shell."""
    done = asyncio.Event()

    async def on_output(params, _conn):
        sys.stdout.write(params["data"])
        sys.stdout.flush()

    async def on_ended(params, _conn):
        done.set()

    conn = await client.connect(user, {"session.output": on_output, "session.ended": on_ended})
    loop = asyncio.get_running_loop()
    try:
        session = await conn.request("session.start", planned)
        await conn.request("session.follow", {"id": session["id"], "offset": 0})
        print(f"shell session {session['id']} as {user}; Ctrl-D to exit", file=sys.stderr)
        while not done.is_set():
            line_task = asyncio.ensure_future(loop.run_in_executor(None, sys.stdin.readline))
            ended = asyncio.ensure_future(done.wait())
            await asyncio.wait({line_task, ended}, return_when=asyncio.FIRST_COMPLETED)
            if done.is_set():
                break
            line = line_task.result()
            try:
                await conn.request("session.write", {"id": session["id"], "data": line or "\x04"})
            except rpc.RpcError:
                break
            if not line:
                await asyncio.wait_for(done.wait(), 15)
    finally:
        await conn.close()
    return 0


def cmd_discover(args) -> int:
    from pathlib import Path

    from .discover import scan

    snap = scan.scan([Path(r) for r in args.root] or None, with_databases=not args.no_databases)
    if args.json:
        _print(snap, True)
        return 0
    dbs = {d["installation"]: d for d in snap["databases"]}
    for inst in snap["installations"]:
        health = {None: "no venv", True: "venv ok", False: "venv BROKEN"}[inst["venv_ok"]]
        print(f"{inst['root']}  Odoo {inst['version'] or '?'}  owner {inst['owner']}  {health}  role {inst['pg_role'] or '-'}")
        entry = dbs.get(inst["root"])
        if entry and entry["error"]:
            print(f"    databases: unavailable ({entry['error']})")
        elif entry:
            print(f"    databases: {', '.join(d['name'] for d in entry['databases']) or 'none'}")
        for conf in snap["instances"]:
            if conf["installation"] == inst["root"]:
                print(f"    instance {conf['name']}  {conf['path']}" + ("  ! " + "; ".join(conf["problems"]) if conf["problems"] else ""))
    orphans = [c for c in snap["instances"] if c["installation"] is None]
    if orphans:
        print("Orphan configs:")
        for conf in orphans:
            print(f"    {conf['path']}  hint {conf['version_hint'] or '-'}")
    for proc in snap["processes"]:
        print(f"process {proc['pid']}  {proc['user']}  port {proc['port']}  instance {proc['instance'] or '-'}  db {proc['database'] or '-'}")
    for unit in snap["units"]:
        print(f"unit {unit['name']}  {unit['active_state']}/{unit['sub_state']}  user {unit['user'] or '-'}")
    for conflict in snap["ports"]["conflicts"]:
        print(f"port conflict {conflict['port']}: {conflict['kind']} pids {conflict['pids']} holder {conflict['holder']}")
    return 0


def cmd_adopt(args) -> int:
    from .discover import registry, scan

    snap = scan.scan(with_databases=False)
    root = os.path.normpath(args.root)
    inst = next((i for i in snap["installations"] if i["root"] == root), None)
    if args.undo:
        print(f"released {root}" if registry.release(root) else f"{root} was not adopted")
        return 0
    if inst is None:
        print(f"odp: {root} is not a discovered Odoo installation", file=sys.stderr)
        return 2
    entry = registry.adopt(inst, args.name)
    print(f"adopted {root} as {entry['name']} (Odoo {entry['version']}). No files were changed.")
    return 0


def cmd_doctor(args) -> int:
    from pathlib import Path

    from . import doctor

    result = doctor.run(roots=[Path(r) for r in args.root] or None, with_databases=not args.no_databases)
    if args.json:
        _print(result, True)
        return 1 if result["counts"]["error"] else 0
    for f in result["findings"]:
        print(f"{f['severity'].upper():7} {f['check']:3} {f['title']}")
        if f["detail"]:
            print(f"        {f['detail']}")
        if args.explain:
            print(f"        why: {f['why']}")
        for command in f["commands"]:
            print("".join(f"        $ {line}\n" if not line.startswith("#") else f"        {line}\n" for line in command.splitlines()), end="")
        if f["repair"] == "venv":
            print(f"        repair: odp repair venv {f['installation']}")
    counts = result["counts"]
    print(f"\n{counts['error']} error(s), {counts['warning']} warning(s)" + ("" if args.explain else ". Add --explain for why each one matters."))
    for item in result["not_checked"]:
        print(f"not checked: {item}")
    return 1 if counts["error"] else 0


def _repair_plan(args):
    from .discover import scan
    from .doctor import repair

    snap = scan.scan(with_databases=False)
    root = os.path.normpath(args.root)
    inst = next((i for i in snap["installations"] if i["root"] == root), None)
    if inst is None:
        print(f"odp: {root} is not a discovered Odoo installation", file=sys.stderr)
        return None
    agent = asyncio.run(client.agent_status(inst["owner"])) if inst.get("owner") else {"state": "stopped"}
    return repair.plan_venv_repair(inst, snap["processes"], python=args.python, custom=not args.no_custom,
                                   carry_extras=args.carry_extras, agent_running=agent["state"] == "running")


def cmd_repair_venv(args) -> int:
    from .doctor import repair

    plan = _repair_plan(args)
    if plan is None:
        return 2
    if args.json and args.plan:
        _print(plan.as_dict(), True)
        return 0 if plan.ok else 1
    print(f"Rebuild the venv of {plan.root} (Odoo {plan.version}) with Python {plan.python}, as {plan.run_as}\n")
    print("Checks")
    for c in plan.checks:
        print(f"  {c.status.upper():4}  {c.id:15} {c.detail}")
    print("\nRequirements: " + (", ".join(plan.requirements) or "none"))
    if plan.extras:
        state = "installed too (--carry-extras)" if plan.carry_extras else "NOT installed; add --carry-extras to install them"
        print(f"In the old venv but in no requirements file ({len(plan.extras)}), {state}:\n  " + " ".join(plan.extras))
    print("\nSteps")
    for step in plan.steps:
        print(f"  {step.phase}. [{step.actor}] {step.title}")
        for command in step.commands:
            print(f"       $ {command}")
    if args.plan:
        return 0 if plan.ok else 1
    if not plan.ok:
        print("\nodp: fix the failed checks first", file=sys.stderr)
        return 1
    if not args.yes and input("\nRebuild now? The old venv stays in service until the swap. [y/N] ").strip().lower() != "y":
        print("cancelled")
        return 1

    def report(event: dict) -> None:
        if event["status"] == "output":
            print(f"    {event['text']}", flush=True)
        elif event["status"] == "start":
            print(f"== {event['step']}: {event['text']}", flush=True)
        elif event["status"] == "ok":
            print(f"== {event['step']}: ok", flush=True)
        else:
            print(f"== {event['step']}: FAILED {event['text']}", file=sys.stderr, flush=True)

    try:
        result = asyncio.run(repair.repair_venv(plan, report))
    except repair.RepairError as exc:
        print(f"odp: repair failed: {exc}. The old venv is in service.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    print(f"{plan.venv} rebuilt." + (f" The old venv is kept as {result['backup']}." if result["backup"] else ""))
    return 0


def _db_report(event: dict) -> None:
    if event["status"] == "output":
        print(f"    {event['text']}", flush=True)
    elif event["status"] == "start":
        print(f"== {event['step']}: {event['text']}", flush=True)
    elif event["status"] == "ok":
        print(f"== {event['step']}: ok" + (f" {event['text']}" if event["text"] else ""), flush=True)
    else:
        print(f"== {event['step']}: FAILED {event['text']}", file=sys.stderr, flush=True)


def cmd_db(args) -> int:
    from .database import context, ops

    try:
        inst, ctx = asyncio.run(context.prepare(args.root))
    except context.NotFound as exc:
        print(f"odp: {exc}", file=sys.stderr)
        return 2
    if args.db_command == "list":
        if getattr(ctx, "listing_error", None):
            print(f"odp: databases could not be listed: {ctx.listing_error}", file=sys.stderr)
            return 1
        if args.json:
            _print(ctx.databases, True)
            return 0
        for d in ctx.databases:
            fs = {True: "filestore", False: "NO filestore", None: "filestore unknown"}[d["filestore_exists"]]
            print(f"{d['name']:30} {d['size'] / 1e6:10.1f} MB  {fs}  {d['filestore'] or ''}")
        return 0
    kind = args.db_command
    plan = ops.plan_db(
        kind, inst, ctx, source=getattr(args, "source", None) or getattr(args, "database", None),
        target=getattr(args, "target", None) or getattr(args, "as_name", None), backup=getattr(args, "backup", None),
        dest=getattr(args, "dest", None), recipe=getattr(args, "recipe", None),
        confirm=getattr(args, "confirm", None),
    )
    if args.json and args.plan:
        _print(plan.as_dict(), True)
        return 0 if plan.ok else 1
    print(f"{kind} on {plan.root} as {plan.run_as}\n\nChecks")
    for c in plan.checks:
        print(f"  {c.status.upper():4}  {c.id:15} {c.detail}")
    print("\nSteps")
    for step in plan.steps:
        print(f"  {step.phase}. [{step.actor}] {step.title}")
        for command in step.commands:
            print(f"       $ {command}")
    if args.plan:
        return 0 if plan.ok else 1
    if not plan.ok:
        print("\nodp: fix the failed checks first", file=sys.stderr)
        return 1
    if not args.yes and kind not in ("drop", "neutralize") and input("\nRun now? [y/N] ").strip().lower() != "y":
        print("cancelled")
        return 1
    try:
        result = asyncio.run(ops.run_db(plan, _db_report))
    except ops.DbError as exc:
        print(f"odp: {kind} failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    print(f"{kind} done. Receipt: {result['receipt']}")
    for key in ("backup", "database", "trash"):
        if result.get(key):
            print(f"  {key}: {result[key]}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="odp", description="Odoo Dev Panel")
    parser.add_argument("--version", action="version", version=f"odp {__version__}")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)

    agent = sub.add_parser("agent", help="manage per-user agents").add_subparsers(dest="agent_command", required=True)
    serve = agent.add_parser("serve", help="run the agent as the current user")
    serve.add_argument("--foreground", action="store_true")
    serve.add_argument("--socket-dir")
    serve.add_argument("--state-dir")
    serve.add_argument("--allow-group", default=paths.DEFAULT_GROUP)
    serve.add_argument("--allow-uid", type=int, action="append", default=[])
    start = agent.add_parser("start", help="start the agent of USER through sudo")
    start.add_argument("--user", "-u", required=True)
    stop = agent.add_parser("stop", help="stop the agent of USER (its sessions keep running)")
    stop.add_argument("--user", "-u", required=True)
    status = agent.add_parser("status", help="show agents")
    status.add_argument("--user", "-u")

    run = sub.add_parser("run", help="start a session as USER: odp run -u odoo19 -- /path/python odoo-bin ...")
    run.add_argument("--user", "-u", required=True)
    run.add_argument("--cwd")
    run.add_argument("--name")
    run.add_argument("argv", nargs=argparse.REMAINDER)

    start = sub.add_parser("start", help="start a discovered instance as its own user, with run flags")
    start.add_argument("instance", help="instance name or config path")
    start.add_argument("--db", "-d")
    start.add_argument("--port", type=int, help="runtime --http-port; the config is not changed")
    start.add_argument("--update", "-u", action="append", default=[], help="module to upgrade (repeatable)")
    start.add_argument("--install", "-i", action="append", default=[], help="module to install (repeatable)")
    start.add_argument("--stop-after-init", action="store_true")
    start.add_argument("--dev", action="append", default=[], help="all, reload, qweb, werkzeug, xml, pdb (repeatable)")
    start.add_argument("--shell", action="store_true", help="interactive odoo-bin shell (needs --db)")
    start.add_argument("--dry-run", action="store_true", help="print the command, start nothing")
    start.add_argument("--arg", dest="extra", action="append", default=[], help="further odoo-bin argument (repeatable)")

    ps = sub.add_parser("ps", help="list sessions")
    ps.add_argument("--user", "-u")

    logs = sub.add_parser("logs", help="print session output")
    logs.add_argument("--user", "-u", required=True)
    logs.add_argument("--follow", "-f", action="store_true")
    logs.add_argument("id")

    stop_s = sub.add_parser("stop", help="stop a session")
    stop_s.add_argument("--user", "-u", required=True)
    stop_s.add_argument("--timeout", type=float, default=15)
    stop_s.add_argument("id")

    discover = sub.add_parser("discover", help="list installations, instances, databases, processes, units, ports")
    discover.add_argument("--root", action="append", default=[], help="directory to scan (repeatable); default /opt /srv ~")
    discover.add_argument("--no-databases", action="store_true")

    adopt = sub.add_parser("adopt", help="remember a discovered installation in the registry; changes no Odoo files")
    adopt.add_argument("root")
    adopt.add_argument("--name")
    adopt.add_argument("--undo", action="store_true", help="remove it from the registry")

    doctor = sub.add_parser("doctor", help="read-only checks: broken venvs, configs, git, ports, units, filestores")
    doctor.add_argument("--root", action="append", default=[], help="directory to scan (repeatable)")
    doctor.add_argument("--no-databases", action="store_true", help="skip PostgreSQL (no filestore check)")
    doctor.add_argument("--explain", action="store_true", help="say why each finding matters")

    repair = sub.add_parser("repair", help="fix a doctor finding").add_subparsers(dest="repair_command", required=True)
    rv = repair.add_parser("venv", help="rebuild the venv of an installation next to the old one, validate, then swap")
    rv.add_argument("root", help="installation root, e.g. /opt/odoo17")
    rv.add_argument("--python", help="Python version (default: the pinned one for the Odoo version)")
    rv.add_argument("--no-custom", action="store_true", help="install only odoo/requirements.txt, not custom repos' files")
    rv.add_argument("--carry-extras", action="store_true", help="also install packages of the old venv that no requirements file names")
    rv.add_argument("--plan", action="store_true", help="dry run: checks and steps. Changes nothing")
    rv.add_argument("--yes", "-y", action="store_true", help="do not ask for confirmation")

    db = sub.add_parser("db", help="databases of an installation: list, backup, restore, clone, drop, neutralize").add_subparsers(
        dest="db_command", required=True)
    dl = db.add_parser("list", help="databases of the installation's PostgreSQL role, with size and filestore")
    dl.add_argument("root", help="installation root, e.g. /opt/odoo17")
    dl.add_argument("--json", action="store_true")

    def db_action(name: str, help_: str):
        a = db.add_parser(name, help=help_)
        a.add_argument("root", help="installation root, e.g. /opt/odoo17")
        a.add_argument("--plan", action="store_true", help="dry run: checks and steps. Changes nothing")
        a.add_argument("--json", action="store_true", help="with --plan: print the plan as JSON")
        a.add_argument("--yes", "-y", action="store_true", help="do not ask for confirmation")
        return a

    a = db_action("backup", "dump a database and archive its filestore into one folder")
    a.add_argument("source")
    a.add_argument("--dest", help="folder for backups, writable by the run-as user (default: its home/odp-backups)")
    a = db_action("restore", "restore a backup folder into a NEW database and filestore")
    a.add_argument("backup", help="absolute path of a backup folder")
    a.add_argument("--as", dest="as_name", required=True, help="name of the new database")
    a = db_action("clone", "copy a database and its filestore under a new name")
    a.add_argument("source")
    a.add_argument("target")
    a.add_argument("--neutralize", nargs="?", const="default", dest="recipe", metavar="RECIPE",
                   help="then run a recipe on the clone only (default recipe, or a name in ~/.config/odoo-dev-panel/recipes)")
    a = db_action("drop", "drop a database; its filestore is moved to a trash folder, not deleted")
    a.add_argument("database")
    a.add_argument("--confirm", help="type the database name to confirm")
    a = db_action("neutralize", "run a recipe on an existing database")
    a.add_argument("database")
    a.add_argument("--recipe", default="default")
    a.add_argument("--confirm", help="type the database name to confirm")

    provision = sub.add_parser("provision", help="create a new Odoo installation").add_subparsers(
        dest="provision_command", required=True
    )
    def spec_args(target):
        target.add_argument("--version", "-V", dest="odoo_version", type=int, required=True, help="Odoo major version, e.g. 17")
        target.add_argument("--dev-user", help="owner of the source trees (default: current user)")
        target.add_argument("--run-as", help="Linux user that runs Odoo (default: odooNN)")
        target.add_argument("--root", help="installation directory (default: /opt/odooNN)")
        target.add_argument("--python", help="Python version (default depends on the Odoo version)")
        target.add_argument("--odoo-git", help="community git URL")
        target.add_argument("--odoo-branch", help="community branch (default: NN.0)")
        target.add_argument("--enterprise-git", help="enterprise git URL (needs access from this machine)")
        target.add_argument("--enterprise-branch")
        target.add_argument("--enterprise-archive", help="enterprise .zip or .tar.* file")
        target.add_argument("--custom", action="append", default=[], metavar="[NAME=]URL[#BRANCH]", help="custom addons repository, repeatable")
        target.add_argument("--config-name", default="default", help="first config file name (default: default)")
        target.add_argument("--pg-host", default="localhost")
        target.add_argument("--pg-port", type=int, default=5432)
        target.add_argument("--http-port", type=int)


    plan = provision.add_parser("plan", help="dry run: preflight checks, steps and the root script. Changes nothing")
    spec_args(plan)
    run_p = provision.add_parser("run", help="create the installation (asks for sudo once)")
    spec_args(run_p)
    run_p.add_argument("--yes", "-y", action="store_true", help="do not ask for confirmation")
    plan.add_argument("--script-only", action="store_true", help="print only the root script")

    sub.add_parser("sidecar", help="JSON-RPC server on stdio for the desktop app")
    sub.add_parser("askpass", help="SUDO_ASKPASS helper")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    # askpass receives a free-form prompt from sudo: bypass argparse.
    if raw[:1] == ["askpass"]:
        from . import askpass

        return askpass.main(raw[1:])
    args = build_parser().parse_args(raw)

    if args.command == "sidecar":
        from . import sidecar

        return sidecar.main()
    if args.command == "agent" and args.agent_command == "serve":
        from .agent import server

        return server.serve(
            socket_dir=args.socket_dir,
            state_dir=args.state_dir,
            allow_group=args.allow_group or None,
            allow_uids=set(args.allow_uid),
            foreground=args.foreground,
        )
    if args.command == "agent" and args.agent_command == "start":
        return privilege.start_agent_interactive(args.user)

    if args.command == "adopt":
        return cmd_adopt(args)
    if args.command == "discover":
        return cmd_discover(args)
    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "repair":
        return cmd_repair_venv(args)
    if args.command == "db":
        try:
            return cmd_db(args)
        except KeyboardInterrupt:
            return 130
    if args.command == "provision" and args.provision_command == "plan":
        return cmd_provision_plan(args)
    if args.command == "provision":
        try:
            return asyncio.run(cmd_provision_run(args))
        except KeyboardInterrupt:
            return 130

    handlers = {
        ("agent", "status"): cmd_agent_status,
        ("agent", "stop"): cmd_agent_stop,
        ("run", None): cmd_run,
        ("start", None): cmd_start,
        ("ps", None): cmd_ps,
        ("stop", None): cmd_stop,
        ("logs", None): cmd_logs,
    }
    handler = handlers[(args.command, getattr(args, "agent_command", None))]
    try:
        return asyncio.run(handler(args))
    except rpc.RpcError as exc:
        print(f"odp: {exc.message}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
