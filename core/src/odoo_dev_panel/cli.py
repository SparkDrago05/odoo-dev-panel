"""``odp`` command line. Every GUI action has a CLI equivalent."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pwd
import shlex
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


def _print_problems(result: dict, start: int) -> None:
    counts = ", ".join(f"{n} {level}" for level, n in result["counts"].items() if n and level != "DEBUG")
    print(f"{counts or 'no Odoo log records'}" + (f" (last part of the log, from byte {start})" if start else ""))
    for g in result["groups"]:
        lines = f"line {g['first_line']}" if g["count"] == 1 else f"lines {g['first_line']}-{g['last_line']}"
        print(f"\n{g['level']:8} x{g['count']:<4} {g['logger']}  ({lines}{', ' + ', '.join(g['dbs']) if g['dbs'] else ''})")
        print(f"  {g['title']}")
        if g["frame"]:
            print(f"  at {g['frame']['file']}:{g['frame']['line']} in {g['frame']['function']}")


async def cmd_logs(args) -> int:
    from . import logs

    done = asyncio.Event()
    level_filter = logs.LevelFilter(args.level) if args.level else None

    def write(data: str) -> None:
        sys.stdout.write(level_filter.feed(data) if level_filter else data)
        sys.stdout.flush()

    async def on_output(params, _conn):
        write(params["data"])

    async def on_ended(params, _conn):
        done.set()

    if args.problems:
        text, start = await _with_agent(args.user, lambda c: logs.read_tail(c, args.id))
        result = logs.analyze(text, args.level or logs.PROBLEM)
        if args.json:
            _print({**result, "start_offset": start}, True)
        else:
            _print_problems(result, start)
        return 0

    if not args.follow:
        async def read_all(conn):
            offset = 0
            while True:
                chunk = await conn.request("session.read", {"id": args.id, "offset": offset})
                if not chunk["data"]:
                    return
                write(chunk["data"])
                offset = chunk["offset"]

        await _with_agent(args.user, read_all)
        if level_filter:
            sys.stdout.write(level_filter.flush())
        return 0

    conn = await client.connect(args.user, {"session.output": on_output, "session.ended": on_ended})
    try:
        await conn.request("session.follow", {"id": args.id, "offset": 0})
        closed = asyncio.ensure_future(conn.closed.wait())
        ended = asyncio.ensure_future(done.wait())
        await asyncio.wait({closed, ended}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        await conn.close()
    if level_filter:
        sys.stdout.write(level_filter.flush())
    return 0


def _provision_params(args) -> dict:
    """The provision request for service.build: flat fields, or a profile with the flags as the top layer."""
    from .provision.spec import CustomRepo

    install = {k: v for k, v in {
        "run_as": args.run_as, "root": args.root, "python": args.python, "odoo_git": args.odoo_git,
        "odoo_branch": args.odoo_branch, "enterprise_git": args.enterprise_git, "enterprise_branch": args.enterprise_branch,
        "enterprise_archive": args.enterprise_archive, "http_port": args.http_port, "config_name": args.config_name,
        "pg_host": args.pg_host, "pg_port": args.pg_port,
    }.items() if v not in (None, "")}
    if not (args.profile or args.dest):
        return {"version": args.odoo_version, "dev_user": args.dev_user, **install, "custom": args.custom}
    overrides: dict = {"install": install}
    if args.custom:
        repos = []
        for text in args.custom:
            c = CustomRepo.parse(text)
            repos.append({k: v for k, v in {"name": c.name, "url": c.url, "branch": c.branch}.items() if v})
        overrides["repos+"] = repos
    dests = {}
    for item in args.dest:
        name, sep, folder = item.partition("=")
        if not sep:
            raise ValueError(f"--dest takes NAME=FOLDER, got {item!r}")
        dests[name] = folder
    return {"profile": args.profile, "version": args.odoo_version, "overrides": overrides, "destinations": dests}


def _spec_from_args(args):
    from .provision import service
    from .provision.spec import CustomRepo, ProvisionSpec, SpecError

    if args.profile or args.dest:
        if args.dev_user:
            print("odp: --dev-user cannot be combined with --profile (the dev user is you)", file=sys.stderr)
            return None
        try:
            return service.build(_provision_params(args))[0]
        except (service.RequestError, ValueError) as exc:
            print(f"odp: {exc}", file=sys.stderr)
            return None
    if args.odoo_version is None:
        print("odp: --version is required (or a --profile that pins one)", file=sys.stderr)
        return None
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
            **{k: v for k, v in {"config_name": args.config_name, "pg_host": args.pg_host, "pg_port": args.pg_port}.items()
               if v is not None},
        )
    except SpecError as exc:
        print(f"odp: {exc}", file=sys.stderr)
        return None


def cmd_provision_plan(args) -> int:
    from .provision import plan as plan_mod
    from .provision import service

    spec = _spec_from_args(args)
    if spec is None:
        return 2
    if args.script_only:
        print(plan_mod.render_root_script(spec, plan_mod.Secrets.generate()), end="")
        return 0
    params = _provision_params(args) if (args.profile or args.dest) else {
        k: v for k, v in _provision_params(args).items() if v not in (None, "", [])}
    try:
        data = service.plan(params, remote=not args.no_remote)
    except service.RequestError as exc:
        print(f"odp: {exc}", file=sys.stderr)
        return 2
    if args.json:
        _print(data, True)
        return 0 if data["ok"] else 1
    print(f"Provision Odoo {spec.version} as {spec.run_as} in {spec.root}  (dry run: nothing is changed)")
    if data["profile"]:
        print(f"Profile: {args.profile or 'none (built-in defaults)'}")
    prev = data["previous"]
    if prev:
        print(f"Previous run ({prev['updated_at']}): {prev['status']}, finished {', '.join(prev['completed']) or 'nothing'}; "
              f"remaining {', '.join(prev['remaining']) or 'nothing'}. Finished parts are reused.")
    print("\nTree")
    for row in data["tree"]:
        print(f"  {spec.root}/{row['path']:40} {row['kind']:10} {row['label']}{'' if row['addons'] else '  (not on addons_path)'}")
    print("\nPreflight")
    for c in data["preflight"]:
        print(f"  {c['status'].upper():4}  {c['id']:22} {c['detail']}")
    print("\nSteps")
    for st in data["steps"]:
        print(f"  {st['phase']}. [{st['actor']:5}] {st['title']}")
        for command in st["commands"]:
            print(f"       $ {command}")
    print(f"\nConfig {spec.conf_path}\n" + "".join(f"  | {line}\n" for line in data["config"].splitlines()))
    print("Root script (run with: odp provision plan ... --script-only)\n")
    print("".join(f"  | {line}\n" for line in data["root_script"].splitlines()))
    return 0 if data["ok"] else 1


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
    if args.snapshot and not (args.update and args.db):
        print("odp: --snapshot needs --update and --db", file=sys.stderr)
        return 2
    if args.dry_run:
        if args.snapshot:
            print(f"first: snapshot of {args.db}")
        print(f"as {user}: {' '.join(planned['argv'])}")
        return 0
    if args.snapshot and (code := await _snapshot_first(planned["meta"]["installation"], args.db)):
        return code
    if args.shell:
        return await _attach_shell(user, planned)
    session = await _with_agent(user, lambda c: c.request("session.start", planned))
    if args.json:
        _print(session, True)
    else:
        port = session["meta"].get("port")
        print(f"started session {session['id']} (pid {session['pid']}) as {user}" + (f", http://localhost:{port}" if port else ""))
    return 0


async def _snapshot_first(root: str, database: str) -> int:
    """Snapshot before an upgrade: the run starts only when the snapshot is complete."""
    from .database import context, ops

    inst, ctx = await context.prepare(root)
    plan = ops.plan_db("snapshot", inst, ctx, source=database)
    if not plan.ok:
        print("odp: no snapshot: " + "; ".join(c.detail for c in plan.checks if c.status == "fail"), file=sys.stderr)
        return 1
    try:
        result = await ops.run_db(plan, _db_report)
    except ops.DbError as exc:
        print(f"odp: snapshot failed, nothing started: {exc}", file=sys.stderr)
        return 1
    print(f"snapshot: {result['backup']}")
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


def cmd_services(args) -> int:
    from . import services

    try:
        if args.services_command in ("list", "status"):
            rows = services.list_services()
            if getattr(args, "name", None):
                rows = [r for r in rows if r["name"] == args.name]
                if not rows:
                    raise services.ServiceError(f"{args.name} is not an Odoo systemd unit known to Odoo Dev Panel")
            if args.json:
                _print(rows, True)
                return 0
            if not rows:
                print("No Odoo systemd units found.")
            for r in rows:
                print(f"{r['name']:32} {r['active_state'] or '?'}/{r['sub_state'] or '?'}  {r['enabled'] or '?':9} user {r['user'] or '-'}  config {r['config'] or '-'}")
            return 0
        if args.services_command == "logs":
            print(services.journal(args.name, args.lines, args.since), end="")
            return 0
        if args.services_command == "show":
            print(services.read_unit(args.name)["text"], end="")
            return 0
        out = services.run_action(args.services_command, args.name)
        if out:
            print(out)
        return 0
    except services.ServiceError as exc:
        print(f"odp: {exc}", file=sys.stderr)
        return 2


def _repo_line(r: dict) -> str:
    st = r.get("state") or {}
    flags = []
    if st.get("dirty"):
        flags.append("dirty")
    if st.get("untracked"):
        flags.append(f"{st['untracked']} untracked")
    if st.get("ahead") or st.get("behind"):
        flags.append(f"+{st.get('ahead') or 0}/-{st.get('behind') or 0}")
    for key in ("detached", "shallow", "foreign", "worktree"):
        if st.get(key):
            flags.append(key)
    where = ", ".join(i["relative"] or i["root"] for i in r["installations"]) or "-"
    if not st:
        return f"{r['path']}  [{r['purpose']}]  ({where})"
    branch = st.get("branch") or (st.get("head") or "")[:12] or "-"
    return f"{r['path']}  [{r['purpose']}]  {branch}  {' '.join(flags) or 'clean'}  ({where})"


def _print_plan(p: dict) -> None:
    for c in p["checks"]:
        print(f"  [{c['status']:4}] {c['detail']}")
    for s in p["steps"]:
        print(f"  {s['title']}")
        for line in s["commands"]:
            print(f"    $ {line}")
    print(f"  {p['counts']['run']} to run, {p['counts']['skip']} skipped")


def cmd_repo(args) -> int:
    """W1-W10: Git repositories of the discovered installations."""
    from .discover import scan
    from .git import api, opener, ops, workspace

    snap = scan.scan(with_databases=False)
    sub = args.repo_command
    try:
        if sub == "list":
            data = workspace.listing(snap, installation=args.installation, with_state=not args.no_state)
            if args.json:
                _print(data, True)
                return 0
            if not data["repos"]:
                print("No Git repositories found in the discovered installations.")
            for r in data["repos"]:
                print(_repo_line(r))
                for prob in (r.get("state") or {}).get("problems", []):
                    if prob["level"] != "info":
                        print(f"    {prob['level']}: {prob['title']}" + (" (heuristic)" if prob["heuristic"] else ""))
            return 0
        if sub == "show":
            data = workspace.show(args.path, snap)
            if args.json:
                _print(data, True)
                return 0
            print(_repo_line(data))
            st = data["state"]
            for name, url in st["remotes"].items():
                print(f"  remote {name}: {url}")
            print(f"  upstream: {st['upstream'] or '-'}   last commit: {st['subject'] or '-'} ({st['committed'] or '-'})")
            for prob in st["problems"]:
                print(f"  {prob['level']}: {prob['title']}. {prob['detail']}")
                for c in prob["commands"]:
                    print(f"    $ {c}")
            return 0
        if sub == "diff":
            data = workspace.diff(args.path, snap, args.file)
            if args.json:
                _print(data, True)
                return 0
            if data["diff"] is not None:
                print(data["diff"], end="")
                return 0
            for f in data["files"]:
                print(f"{(f['index'] or ' ')}{(f['worktree'] or ' ')} {f['path']}")
            for key in ("incoming", "outgoing"):
                for c in data[key]:
                    print(f"{key[:2]} {c['sha']} {c['subject']}")
            return 0
        if sub == "open":
            print(shlex.join(opener.open_path(workspace.resolve(args.path, snap)["path"], args.target)))
            return 0
        if sub == "add":
            if args.path:
                out = api.register_existing({"path": os.path.abspath(args.path), "installation": args.installation,
                                             "fields": _assoc_fields(args)}, snap)
                print(f"registered {out['repo']}" + (f" for {args.installation}" if args.installation else ""))
                return 0
            params = {"installation": args.installation, "url": args.url, "destination": args.dest, "ref": args.ref,
                      "shallow": not args.full, "fields": _assoc_fields(args)}
            plan = api.plan("clone", params, snap)
        elif sub == "apply":
            plan = api.plan("bundle", {"installation": args.installation, "profile": args.profile}, snap)
        elif sub == "addons":
            return _repo_addons(args, snap)
        elif sub == "forget":
            ok = api.forget({"path": os.path.abspath(args.path), "installation": args.installation})
            print("forgotten (no files were touched)" if ok else "not registered")
            return 0
        elif sub in ("fetch", "pull"):
            params = {"bulk": True, "installation": args.installation} if args.bulk else \
                {"repos": [os.path.abspath(p) for p in args.paths]}
            plan = api.plan(sub, params, snap)
        elif sub == "switch":
            plan = api.plan("switch", {"repo": os.path.abspath(args.path), "branch": args.branch,
                                       "fetch_branch": args.fetch_branch}, snap)
        else:
            plan = api.plan("checkout", {"repo": os.path.abspath(args.path), "ref": args.ref, "confirm": args.confirm}, snap)
    except (api.ApiError, api.NotFound, LookupError, opener.OpenError) as exc:
        print(f"odp: {exc}", file=sys.stderr)
        return 2
    data = plan.as_dict()
    if args.plan:
        _print(data, args.json) if args.json else _print_plan(data)
        return 0 if plan.ok else 1
    if not args.json:
        _print_plan(data)
    if not plan.ok:
        print("odp: nothing can run; see the checks above", file=sys.stderr)
        return 1
    if not args.yes and input("Run now? [y/N] ").strip().lower() != "y":
        return 1

    def report(event: dict) -> None:
        if not args.json and event["status"] == "output":
            print(f"  {event['text']}")
        elif not args.json and event["status"] == "start":
            print(f"== {event['text']}")

    try:
        result = asyncio.run(ops.run_plan(plan, report))
    except KeyboardInterrupt:
        return 130
    if args.json:
        _print(result, True)
    else:
        for r in result["results"]:
            why = (r["problem"] or {}).get("title") or r["reason"] or ""
            print(f"{r['status']:9} {r['repo']}  {why}")
            if r.get("changed_modules"):
                print(f"          changed modules (review before upgrading): {', '.join(r['changed_modules'])}")
            if r.get("addons_path_entry"):
                print(f"          add to addons_path if wanted: {r['addons_path_entry']}")
        print(", ".join(f"{v} {k}" for k, v in result["counts"].items() if v))
        done = [r["repo"] for r in result["results"] if r["status"] in ("ok", "kept") and r.get("addons_path_entry")]
        if plan.op == "bundle" and done:
            print(f"Review addons_path changes: odp repo addons {args.installation} " + " ".join(shlex.quote(d) for d in done))
    return 0 if not result["counts"]["failed"] else 1


def _repo_addons(args, snap) -> int:
    """Show, per config, what the repositories would add to addons_path; with --apply CONFIG write those configs."""
    from .git import api

    rows = api.addons_proposal({"installation": args.installation, "repos": [os.path.abspath(r) for r in args.repos]}, snap)
    if args.json and not args.apply:
        _print(rows, True)
        return 0
    for row in rows:
        if row["error"]:
            print(f"{row['path']}: {row['error']}")
        elif row["add"]:
            print(f"{row['path']}: add {', '.join(row['add'])}" + ("" if row["writable"] else "  (not writable by you)"))
        else:
            print(f"{row['path']}: nothing to add")
    targets = [r for r in rows if r["path"] in args.apply and r["add"]]
    missing = set(args.apply) - {r["path"] for r in rows}
    if missing:
        print(f"odp: not a config of {args.installation}: {', '.join(sorted(missing))}", file=sys.stderr)
        return 2
    if not targets:
        return 0
    if not args.yes and input(f"Update {len(targets)} config(s)? A backup of each is kept. [y/N] ").strip().lower() != "y":
        return 1
    status = 0
    for row in targets:
        try:
            out = api.addons_apply({"path": row["path"], "add": row["add"], "sha": row["sha"]}, snap)
            print(f"{row['path']}: updated, backup {out['backup']}")
        except (api.ApiError, api.NotFound) as exc:
            print(f"{row['path']}: {exc}", file=sys.stderr)
            status = 1
    return status


def cmd_profile(args) -> int:
    """T2/T6: saved profiles and repository bundles."""
    from .provision import export, profiles

    sub = args.profile_command
    try:
        if sub == "list":
            rows = profiles.list_profiles()
            org = profiles.org_path()
            if args.json:
                _print({"profiles": rows, "folder": str(profiles.profiles_dir()), "org": str(org), "org_exists": org.is_file()}, True)
                return 0
            print(f"Org overlay: {org} ({'present' if org.is_file() else 'not present'})")
            if not rows:
                print(f"No profiles in {profiles.profiles_dir()}")
            for r in rows:
                pin = f"Odoo {r['odoo_version']}" if r["odoo_version"] else "any version"
                print(f"{r['name']:24} {pin:12} {r['repos']} repo(s)  {r['title'] or ''}" + (f"  ! {r['error']}" if r["error"] else ""))
            return 0
        if sub == "show":
            data = profiles.resolve(args.name, args.odoo_version)
            if args.json:
                _print(data, True)
                return 0
            print(f"{data['name'] or args.name}: Odoo {data['version']}, root {data['root']}, run as {data['run_as']}")
            for key, value in sorted(data["install"].items()):
                print(f"  install.{key} = {value}   ({data['origin'].get('install.' + key)})")
            for key, value in sorted(data["config"].items()):
                print(f"  config.{key} = {value}   ({data['origin'].get('config.' + key)})")
            for n, repo in enumerate(data["repos"]):
                print(f"  repo {repo.get('name') or repo['url']}: {repo['url']} {repo.get('branch') or '(default branch)'} "
                      f"-> {repo.get('destination') or 'custom/<name>'}   ({data['origin'].get(f'repos.{n}')})")
            return 0
        if sub == "import":
            print(f"saved {profiles.import_file(os.path.abspath(args.file), args.name, args.overwrite)}")
            return 0
        if sub == "delete":
            moved = profiles.delete(args.name)
            print(f"moved to {moved}" if moved else f"no profile {args.name}")
            return 0 if moved else 1
        if sub == "org":
            print(profiles.set_org_path(None if args.default else os.path.abspath(args.path)) if (args.default or args.path)
                  else profiles.org_path())
            return 0
        from .discover import scan

        data, notes = export.export_installation(os.path.normpath(args.root), scan.scan(with_databases=False))
        text = profiles.dump(data)
        if args.name:
            print(f"saved {profiles.save(args.name, data, args.overwrite)}")
        else:
            print(text, end="")
        for note in notes:
            print(f"# note: {note}", file=sys.stderr)
        return 0
    except (profiles.ProfileError, export.ExportError) as exc:
        print(f"odp: {exc}", file=sys.stderr)
        return 2


def _assoc_fields(args) -> dict:
    fields = {"purpose": args.purpose, "group": args.group, "preferred_branch": args.preferred_branch}
    if args.no_addons:
        fields["addons"] = False
    if args.no_bulk:
        fields["bulk"] = False
    return {k: v for k, v in fields.items() if v is not None}


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


def cmd_repair_perms(args) -> int:
    from .discover import scan
    from .doctor import permissions
    from .provision import execute

    try:
        plan = permissions.plan_config_perms(scan.scan(with_databases=False), args.root)
    except permissions.PermissionsError as exc:
        print(f"odp: {exc}", file=sys.stderr)
        return 2
    if args.json and args.plan:
        _print(plan.as_dict(), True)
        return 0
    print(f"Standard permissions for the configs of {plan.root} (run as {plan.run_as}, edited by {plan.dev_user})\n")
    for c in plan.changes:
        print(f"  {c.kind:6} {c.path}\n         {c.current}  ->  {c.target}")
    for path in plan.kept:
        print(f"  ok     {path}")
    for note in plan.notes:
        print(f"  note   {note}")
    if not plan.changes:
        print("\nNothing to change.")
        return 0
    print("\nRoot script (one sudo call):\n" + plan.script)
    if args.plan:
        return 0
    if not args.yes and input("Apply? [y/N] ").strip().lower() != "y":
        print("cancelled")
        return 1

    async def root_runner(script: str, report) -> int:
        return await execute.sudo_runner(script, report)

    try:
        result = asyncio.run(permissions.apply_config_perms(plan, _db_report, root_runner))
    except permissions.PermissionsError as exc:
        print(f"odp: {exc}", file=sys.stderr)
        return 1
    print(f"{result['changed']} path(s) changed. Receipt with the old values: {result['receipt']}")
    return 0


def _read_text(path: str) -> str:
    with open(path) as fh:
        return fh.read()


def cmd_config(args) -> int:
    from . import configedit
    from .discover import scan

    snap = scan.scan(with_databases=False)
    path = os.path.abspath(args.path)
    inst = next((i for i in snap["instances"] if i["path"] == path), None)
    if inst is None:
        print(f"odp: {path} is not a discovered Odoo config", file=sys.stderr)
        return 2
    try:
        if args.config_command == "show":
            opened = configedit.open_config(path, args.reveal, snap)
            sys.stdout.write(opened.text)
            return 0
        if args.config_command == "check":
            issues = configedit.validate(_read_text(path), snap, path)
            if args.json:
                _print([vars(i) for i in issues], True)
            for i in issues if not args.json else []:
                print(f"{i.level.upper():7} {i.key or '-':18} {i.text}")
            if not issues and not args.json:
                print("ok")
            return 1 if any(i.level == "error" for i in issues) else 0
        if args.config_command == "copy":
            owner = next((i.get("owner") for i in snap["installations"] if i["root"] == inst.get("installation")), None)
            result = configedit.copy(path, args.name, owner == pwd.getpwuid(os.getuid()).pw_name,
                                     configedit.copy_folder(path, inst.get("installation")))
            print(result["path"])
            if result["warning"]:
                print(f"warning: {result['warning']}", file=sys.stderr)
            return 0
        if args.config_command == "edit":
            return _config_edit(path, snap)
        if args.config_command == "set":
            return _config_set(path, snap, args.assignments, args.unset)
    except configedit.ConfigError as exc:
        print(f"odp: {exc}", file=sys.stderr)
        return 1
    return 2


def _config_edit(path: str, snap: dict) -> int:
    """$EDITOR on a private copy with secrets masked; validated and saved with a backup."""
    import subprocess
    import tempfile

    from . import configedit

    opened = configedit.open_config(path, False, snap)
    if not opened.access.writable:
        print(f"odp: you cannot write {path} ({opened.access.owner}:{opened.access.group} {opened.access.mode}). "
              "Run: odp repair config-perms <installation root>", file=sys.stderr)
        return 1
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nano"
    with tempfile.TemporaryDirectory(prefix="odp-config-") as tmp:
        work = os.path.join(tmp, os.path.basename(path))
        with open(os.open(work, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as fh:
            fh.write(opened.text)
        while True:
            if subprocess.call([*shlex.split(editor), work]) != 0:
                print("odp: editor failed; nothing saved", file=sys.stderr)
                return 1
            text = _read_text(work)
            issues = configedit.validate(configedit.unmask(text, _read_text(path)), snap, path)
            for i in issues:
                print(f"{i.level.upper():7} {i.key or '-':18} {i.text}")
            errors = [i for i in issues if i.level == "error"]
            try:
                answer = input("Fix errors in the editor? [Y/n] " if errors else "Save? [y/N/e(dit again)] ").strip().lower()
            except EOFError:  # no terminal: never save on a guess
                answer = "n"
            if errors and answer in ("", "y"):
                continue
            if errors or answer != "y":
                if answer == "e":
                    continue
                print("not saved")
                return 1
            result = configedit.save(path, text, opened.sha, snap)
            print(f"saved; previous version in {result['backup']}" if result["changed"] else "no change")
            return 0


def _config_set(path: str, snap: dict, assignments: list[str], unset: list[str]) -> int:
    """Set or remove options in place, validate, save with a backup."""
    from . import configedit

    changes: dict[str, str | None] = {}
    for item in assignments:
        key, sep, value = item.partition("=")
        if not sep:
            print(f"odp: {item!r} is not KEY=VALUE", file=sys.stderr)
            return 2
        changes[key.strip()] = value.strip()
    changes.update({key: None for key in unset})
    if not changes:
        print("odp: nothing to set: give KEY=VALUE or --unset KEY", file=sys.stderr)
        return 2
    current = _read_text(path)
    text = configedit.set_options(current, changes)
    issues = configedit.validate(text, snap, path)
    for i in issues:
        print(f"{i.level.upper():7} {i.key or '-':18} {i.text}", file=sys.stderr)
    if any(i.level == "error" for i in issues):
        print("not saved", file=sys.stderr)
        return 1
    result = configedit.save(path, text, configedit.sha(current), snap)
    print(f"saved; previous version in {result['backup']}" if result["changed"] else "no change")
    return 0


def cmd_modules(args) -> int:
    from . import configedit, modules
    from .discover import scan

    snap = scan.scan(with_databases=False)
    path = os.path.abspath(args.config)
    if not any(i["path"] == path for i in snap["instances"]):
        print(f"odp: {path} is not a discovered Odoo config", file=sys.stderr)
        return 2
    try:
        full = modules.for_config(path, snap, args.module, args.depth)
    except KeyError as exc:
        print(f"odp: no module {exc.args[0]} in the addons_path", file=sys.stderr)
        return 1
    except (configedit.ConfigError, OSError) as exc:
        print(f"odp: {exc}", file=sys.stderr)
        return 1
    if args.json:
        _print(full, True)
        return 0
    if args.module:
        f = full["focus"]
        print(f"{f['name']}  {f['module'].get('version', '-')}  {f['module']['path']}")
        for title, key in (("needs", "needs"), ("needed by (breaks if removed)", "needed_by")):
            items = sorted(f[key].items(), key=lambda kv: (kv[1], kv[0]))
            print(f"{title}: " + (", ".join(f"{n}" + (f" ({d})" if d > 1 else "") for n, d in items) or "-"))
        for mod, deps in f["missing"].items():
            print(f"MISSING  {mod} needs {', '.join(deps)}")
    else:
        for name, m in sorted(full["modules"].items()):
            print(f"{name:32} depends {len(m['depends']):3}  required by {len(m['required_by']):3}"
                  + ("" if m["installable"] else "  not installable"))
        for mod, deps in full["missing"].items():
            print(f"MISSING  {mod} needs {', '.join(deps)}")
    for s in full["shadowed"]:
        print(f"shadowed  {s['path']} (the one in {os.path.dirname(s['by'])} wins)")
    for p in full["unreadable"]:
        print(f"unreadable manifest  {p}")
    for c in full["cycles"]:
        print("CYCLE  " + " -> ".join(c))
    return 0


MODULE_VERBS = ("graph", "changed", "check", "upgrade", "install", "test", "tests", "drop-test", "scaffold")


def _module_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="odp modules", description="module development: odp modules VERB ... "
                                "(odp modules CONFIG [MODULE] still shows the dependency graph)")
    verbs = p.add_subparsers(dest="verb", required=True)
    g = verbs.add_parser("graph", help="module dependencies of a config (same as odp modules CONFIG)")
    g.add_argument("config")
    g.add_argument("module", nargs="?")
    g.add_argument("--depth", type=int)
    g.add_argument("--json", action="store_true")
    c = verbs.add_parser("changed", help="modules touched by uncommitted work, with guidance (heuristic)")
    c.add_argument("config")
    c.add_argument("--json", action="store_true")
    c = verbs.add_parser("check", help="manifest checks of the config's own modules (or the named ones)")
    c.add_argument("config")
    c.add_argument("modules", nargs="*")
    c.add_argument("--json", action="store_true")
    for name, text in (("upgrade", "-u modules in a database, snapshot first, then stop"),
                       ("install", "-i modules in a database, snapshot first, then stop")):
        u = verbs.add_parser(name, help=text)
        u.add_argument("config")
        u.add_argument("modules", nargs="+")
        u.add_argument("-d", "--database", required=True)
        u.add_argument("--no-snapshot", action="store_true", help="skip the snapshot before the change")
        u.add_argument("--plan", action="store_true")
        u.add_argument("--yes", action="store_true")
        u.add_argument("--json", action="store_true")
    t = verbs.add_parser("test", help="run module tests in a new odp_test_* database (dropped if they pass)")
    t.add_argument("config")
    t.add_argument("modules", nargs="+")
    t.add_argument("--tags", help="--test-tags value (default: /module for each module)")
    t.add_argument("--no-demo", action="store_true", help="without demo data")
    t.add_argument("--plan", action="store_true")
    t.add_argument("--yes", action="store_true")
    t.add_argument("--json", action="store_true")
    h = verbs.add_parser("tests", help="test run history")
    h.add_argument("--installation")
    h.add_argument("--json", action="store_true")
    d = verbs.add_parser("drop-test", help="drop the test database a failed run kept")
    d.add_argument("id")
    s = verbs.add_parser("scaffold", help="create a minimal module in FOLDER (inside the installation or its repositories)")
    s.add_argument("installation")
    s.add_argument("folder")
    s.add_argument("name")
    s.add_argument("--title")
    s.add_argument("--depends", help="comma-separated (default: base)")
    s.add_argument("--plan", action="store_true")
    s.add_argument("--yes", action="store_true")
    return p


def _job_report(event: dict) -> None:
    if event["status"] == "output":
        print(f"    {event['text']}", flush=True)
    elif event["status"] == "start":
        print(f"== {event['step']}: {event['text']}", flush=True)
    else:
        print(f"== {event['step']}: {event['status']} {event['text']}".rstrip(), flush=True)


def cmd_module_verb(argv: list[str]) -> int:
    """odp modules VERB ... (M1-M9)."""
    from .discover import scan
    from .module_center import actions, api

    args = _module_parser().parse_args(argv)
    if args.verb == "graph":
        ns = argparse.Namespace(config=args.config, module=args.module, depth=args.depth, json=args.json)
        return cmd_modules(ns)
    snap = scan.scan(with_databases=args.verb in ("upgrade", "install"))
    try:
        if args.verb in ("changed", "check"):
            graph = asyncio.run(api.load({"config": os.path.abspath(args.config)}, snap, with_changes=args.verb == "changed"))
            if args.verb == "changed":
                data = graph["changed"]
                if args.json:
                    _print(data, True)
                    return 0
                for repo, err in data["errors"].items():
                    print(f"{repo}: {err}", file=sys.stderr)
                if not data["modules"]:
                    print("No module has uncommitted changes.")
                for name, e in sorted(data["modules"].items()):
                    tag = "new" if e["new"] else "removed" if e["removed"] else ",".join(e["kinds"]) or "dependency"
                    print(f"{name:32} {tag:24} {e['action']:16} {e['why']}")
                print("(guidance from changed file kinds, not a verdict)")
                return 0
            wanted = args.modules or [n for n, i in graph["modules"].items() if i.get("own")]
            rows = {n: graph["modules"][n]["problems"] for n in wanted if n in graph["modules"]}
            missing = [n for n in wanted if n not in graph["modules"]]
            if args.json:
                _print({"problems": rows, "unknown": missing}, True)
            else:
                for n in missing:
                    print(f"{n}: not on this config's addons_path")
                for n, probs in sorted(rows.items()):
                    for pr in probs:
                        print(f"{n:32} {pr['level']:5} {pr['text']}")
                bad = sum(1 for probs in rows.values() for pr in probs if pr["level"] == "error")
                print(f"{len(rows)} module(s) checked, {bad} error(s)")
            return 1 if missing or any(pr["level"] == "error" for probs in rows.values() for pr in probs) else 0
        if args.verb == "tests":
            rows = [r for r in actions.history() if not args.installation or r["installation"] == args.installation]
            if args.json:
                _print(rows, True)
                return 0
            for r in rows:
                print(f"{r['at']}  {r['status']:8} {r['tests']:4} tests {r['failures']} failed {r['errors']} errors  "
                      f"{','.join(r['modules'])}  {r['database'] + ' (kept)' if r['kept'] else ''}  id {r['id']}")
            return 0
        if args.verb == "drop-test":
            asyncio.run(actions.drop_kept(args.id, _job_report))
            print("dropped")
            return 0
        if args.verb == "scaffold":
            deps = [d.strip() for d in args.depends.split(",")] if args.depends else None
            planned = actions.scaffold_plan(snap, os.path.normpath(args.installation), os.path.abspath(args.folder),
                                            args.name, args.title, deps)
            print(f"Create {planned['target']}:")
            for rel in planned["files"]:
                print(f"  {rel}")
            if args.plan:
                return 0 if planned["ok"] else 1
            if not args.yes and input("Create? [y/N] ").strip().lower() != "y":
                return 1
            for path in actions.scaffold_create(planned):
                print(f"created {path}")
            return 0
        params = {"config": os.path.abspath(args.config), "modules": args.modules}
        if args.verb == "test":
            params.update(tags=args.tags, demo=not args.no_demo)
        else:
            params.update(database=args.database, snapshot=not args.no_snapshot)
        planned = asyncio.run(api.plan(args.verb, params, snap))
    except (api.ApiError, OSError) as exc:
        print(f"odp: {exc}", file=sys.stderr)
        return 2
    shown = {k: v for k, v in planned.items() if k != "session"} | {"argv": planned["session"]["argv"]}
    if args.json and args.plan:
        _print(shown, True)
        return 0 if planned["ok"] else 1
    if not args.json:
        for c in planned["checks"]:
            print(f"  [{c['status']:4}] {c['detail']}")
        for st in planned["steps"]:
            print(f"  {st['phase']}. {st['title']}")
            for line in st["commands"]:
                print(f"     $ {line}")
    if args.plan:
        return 0 if planned["ok"] else 1
    if not planned["ok"]:
        print("odp: fix the failed checks first", file=sys.stderr)
        return 1
    if not args.yes and input("Run now? [y/N] ").strip().lower() != "y":
        return 1

    async def go():
        conn = await client.connect(planned["session"]["user"])
        try:
            return await api.execute(planned, conn, (lambda e: None) if args.json else _job_report)
        finally:
            await conn.close()

    try:
        result = asyncio.run(go())
    except (api.ApiError, rpc.RpcError) as exc:
        print(f"odp: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    if args.json:
        _print(result, True)
    elif planned["kind"] == "test":
        print(f"{result['status']}: {result['tests']} tests, {result['failures']} failed, {result['errors']} errors"
              + (f"; database {result['database']} kept (odp modules drop-test {result['id']})" if result["kept"] else ""))
        for f in result["failed"]:
            print(f"  {f['kind'].upper()} {f['test']}")
    else:
        print(f"exit code {result['exit_code']}" + (f", snapshot {result['backup']}" if result["backup"] else ""))
    ok = result.get("status") == "passed" if planned["kind"] == "test" else result["exit_code"] == 0
    return 0 if ok else 1


def cmd_docker(args) -> int:
    from .discover import docker

    command = getattr(args, "docker_command", None) or "list"
    if command in ("new", "delete"):
        return _docker_stack(args, command)
    if command != "list":
        return _docker_action(args, command)
    result = docker.discover_docker()
    if args.json:
        _print(result, True)
        return 0 if result["available"] else 1
    if result["error"]:
        print(f"odp: {result['error']}", file=sys.stderr)
        return 1
    if not result["containers"]:
        print("no Odoo containers")
    for c in result["containers"]:
        where = f"compose {c['compose']['project']}/{c['compose']['service']}" if c["compose"] else "docker run"
        ports = ", ".join(f"{p['host_port']}->{p['container']}" for p in c["ports"]) or "no published ports"
        print(f"{c['name']}  {c['image']}  Odoo {c['version'] or '?'}  {c['status']}  {where}  {ports}")
        for label, m in [("config", c["config"]), ("data", c["data"]), *(("addons", a) for a in c["addons"])]:
            if m:
                kind = "anonymous volume" if m["anonymous"] else "volume"
                host = m["host"] or (f"{kind} {m['volume']}" if m["volume"] else "inside the image")
                print(f"    {label:7} {m['container']} = {host}")
        if c["db"]["container"] or c["db"]["host"]:
            print(f"    db      {c['db']['container'] or c['db']['host']}" + (f" as {c['db']['user']}" if c["db"]["user"] else ""))
    return 0


def _docker_target(name: str) -> tuple[dict, list[dict]] | None:
    from . import dockerops
    from .discover import docker

    found = docker.discover_docker()
    if found["error"]:
        print(f"odp: {found['error']}", file=sys.stderr)
        return None
    try:
        return dockerops.find(found["containers"], name), found["containers"]
    except dockerops.DockerError as exc:
        print(f"odp: {exc}", file=sys.stderr)
        return None


def _docker_stack(args, command: str) -> int:
    from . import dockerops, dockerprov
    from .discover import docker

    found = docker.discover_docker()
    if found["error"]:
        print(f"odp: {found['error']}", file=sys.stderr)
        return 1
    try:
        plan = (dockerprov.plan_new(args.name, args.version, found["containers"], args.port, args.addons) if command == "new"
                else dockerprov.plan_delete(args.container, found["containers"], args.confirm))
    except dockerops.DockerError as exc:
        print(f"odp: {exc}", file=sys.stderr)
        return 2
    if args.json and args.plan:
        _print(plan.as_dict(), True)
        return 0 if plan.ok else 1
    print(f"{command}\n\nChecks")
    for c in plan.checks:
        print(f"  {c.status.upper():4}  {c.id:10} {c.detail}")
    print("\nSteps")
    for step in plan.steps:
        print(f"  {step.phase}. {step.title}")
        for line in step.commands:
            print(f"       $ {line}")
    if args.plan:
        return 0 if plan.ok else 1
    if not plan.ok:
        print("\nodp: fix the failed checks first", file=sys.stderr)
        return 1
    try:
        result = asyncio.run((dockerprov.run_new if command == "new" else dockerprov.run_delete)(plan, _db_report))
    except dockerops.DockerError as exc:
        print(f"odp: {command} failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    if command == "new":
        print(f"{result['container']} is up: http://localhost:{result['port']}  (folder {result['folder']})")
    else:
        print(f"deleted stack {result['name']}")
    return 0


def _docker_action(args, command: str) -> int:
    from . import dockerops

    target = _docker_target(args.container)
    if target is None:
        return 2
    container, everything = target
    try:
        if command == "logs":
            if args.follow:
                os.execvp("docker", dockerops.logs_argv(container["name"], args.tail, follow=True))
            text = dockerops.read_logs(container["name"], args.tail)
            if args.problems:
                from . import logs

                for g in logs.analyze(text)["groups"]:
                    print(f"{g['level']:8} x{g['count']:<4} {g['title']}")
                return 0
            sys.stdout.write(text)
            return 0
        if command == "shell":
            os.execvp("docker", dockerops.shell_argv(container, args.database))
        plan = dockerops.plan_action(command, container, everything, database=getattr(args, "db", None),
                                     update=getattr(args, "update", None), install=getattr(args, "install", None),
                                     demo=not getattr(args, "no_demo", False))
    except dockerops.DockerError as exc:
        print(f"odp: {exc}", file=sys.stderr)
        return 1
    if args.json and args.plan:
        _print(plan.as_dict(), True)
        return 0 if plan.ok else 1
    print(f"{command} {plan.container}\n\nChecks")
    for c in plan.checks:
        print(f"  {c.status.upper():4}  {c.id:12} {c.detail}")
    print("\nSteps")
    for step in plan.steps:
        print(f"  {step.phase}. {step.title}")
        for line in step.commands:
            print(f"       $ {line}")
    if args.plan:
        return 0 if plan.ok else 1
    if not plan.ok:
        print("\nodp: fix the failed checks first", file=sys.stderr)
        return 1
    try:
        asyncio.run(dockerops.run_action(plan, _db_report))
    except dockerops.DockerError as exc:
        print(f"odp: {command} failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    print(f"{command} done")
    return 0


def cmd_compare(args) -> int:
    from . import compare
    from .discover import scan

    snap = scan.scan(with_databases=False)
    paths = [os.path.abspath(p) for p in (args.a, args.b)]
    for p in paths:
        if not any(i["path"] == p for i in snap["instances"]):
            print(f"odp: {p} is not a discovered Odoo config", file=sys.stderr)
            return 2
    result = compare.compare(paths[0], paths[1], snap)
    if args.json:
        _print(result, True)
        return 0 if _same(result) else 1
    a, b = result["a"], result["b"]
    print(f"A  {a['name']}  {a['path']}\nB  {b['name']}  {b['path']}")
    for n in a["notes"] + b["notes"]:
        print(f"note: {n}")
    for f in result["facts"]:
        print(f"{'  ' if f['same'] else '! '}{f['key']:10} {f['a'] if f['a'] is not None else '-'}" + ("" if f["same"] else f"  ->  {f['b'] if f['b'] is not None else '-'}"))
    for e in result["addons"]["only_a"]:
        print(f"addons_path only in A: {e}")
    for e in result["addons"]["only_b"]:
        print(f"addons_path only in B: {e}")
    for title, section in (("package", result["packages"]), ("option", result["options"])):
        if section is None:
            print(f"{title}s: not compared (no readable venv on one side)")
            continue
        for k, v in section["only_a"].items():
            print(f"{title} only in A: {k} {v}")
        for k, v in section["only_b"].items():
            print(f"{title} only in B: {k} {v}")
        for k, (x, y) in section["changed"].items():
            dim = "  (expected)" if title == "option" and k in result["expected"] else ""
            print(f"{title} differs: {k}  {x} -> {y}{dim}")
    return 0 if _same(result) else 1


def _same(r: dict) -> bool:
    sections = [s for s in (r["packages"], r["options"]) if s]
    return all(f["same"] for f in r["facts"]) and not r["addons"]["only_a"] and not r["addons"]["only_b"] and \
        all(not (s["only_a"] or s["only_b"] or s["changed"]) for s in sections)


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
    from . import dockerdb, dockerops
    from .database import context, ops

    docker_root = args.root.startswith("docker:")  # docker:<container>: its database container, same actions
    try:
        if docker_root:
            inst, ctx = asyncio.run(dockerdb.prepare(args.root[len("docker:"):]))
            ops = dockerdb  # same interface: plan_db, run_db, DbError
            ctx.listing_error = ctx.list_error  # type: ignore[attr-defined]
        else:
            inst, ctx = asyncio.run(context.prepare(args.root))
    except (context.NotFound, dockerops.DockerError) as exc:
        print(f"odp: {exc}", file=sys.stderr)
        return 2
    if args.db_command == "snapshots":
        try:
            snaps = dockerdb.list_snapshots(inst["name"], args.database, ctx.home) if docker_root \
                else asyncio.run(ops.list_snapshots(inst, ctx, args.database))
        except ops.DbError as exc:
            print(f"odp: snapshots could not be listed: {exc}", file=sys.stderr)
            return 1
        if args.json:
            _print(snaps, True)
            return 0
        for x in snaps:
            size = f"{x['bytes'] / 1e6:10.1f} MB" if x["bytes"] is not None else " " * 13
            print(f"{x['database'] or '?':30} {x['created_at'] or '':26} {size}  {x['path']}")
        if not snaps:
            print("no snapshots")
        return 0
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
        confirm=getattr(args, "confirm", None), keep=getattr(args, "keep", None),
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
    if not args.yes and kind not in ("drop", "neutralize", "revert") and input("\nRun now? [y/N] ").strip().lower() != "y":
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
    for key in ("backup", "database", "trash", "aside", "aside_fs", "removed"):
        if result.get(key):
            print(f"  {key}: {result[key]}")
    for path in result.get("pruned") or []:
        print(f"  removed old snapshot: {path}")
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
    start.add_argument("--snapshot", action="store_true", help="with --update: snapshot the database first (odp db snapshots)")
    start.add_argument("--arg", dest="extra", action="append", default=[], help="further odoo-bin argument (repeatable)")

    ps = sub.add_parser("ps", help="list sessions")
    ps.add_argument("--user", "-u")

    logs = sub.add_parser("logs", help="print session output")
    logs.add_argument("--user", "-u", required=True)
    logs.add_argument("--follow", "-f", action="store_true")
    logs.add_argument("--level", "-l", type=str.upper, choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                      help="only records at this level or above, with their tracebacks")
    logs.add_argument("--problems", "-p", action="store_true",
                      help="group warnings and errors (or --level and above) instead of printing the log")
    logs.add_argument("id")

    stop_s = sub.add_parser("stop", help="stop a session")
    stop_s.add_argument("--user", "-u", required=True)
    stop_s.add_argument("--timeout", type=float, default=15)
    stop_s.add_argument("id")

    discover = sub.add_parser("discover", help="list installations, instances, databases, processes, units, ports")
    discover.add_argument("--root", action="append", default=[], help="directory to scan (repeatable); default /opt /srv ~")
    discover.add_argument("--no-databases", action="store_true")

    svc = sub.add_parser("services", help="Odoo systemd units: list, start, stop, restart, enable, disable, logs, show").add_subparsers(
        dest="services_command", required=True
    )
    for name, text in (("list", "list Odoo units with state"), ("status", "state of one unit")):
        p = svc.add_parser(name, help=text)
        p.add_argument("name", nargs="?" if name == "list" else None)
        p.add_argument("--json", action="store_true")
    for name in ("start", "stop", "restart", "enable", "disable", "show"):
        svc.add_parser(name, help=f"{name} an Odoo unit" if name != "show" else "print the unit file").add_argument("name")
    logs = svc.add_parser("logs", help="journal of an Odoo unit")
    logs.add_argument("name")
    logs.add_argument("-n", "--lines", type=int, default=200)
    logs.add_argument("--since")

    repo = sub.add_parser("repo", help="Git repositories of the installations: list, show, diff, add, fetch, pull, switch").add_subparsers(
        dest="repo_command", required=True
    )
    r = repo.add_parser("list", help="repositories with branch, changes and sync state (read-only)")
    r.add_argument("--installation", help="only this installation root")
    r.add_argument("--no-state", action="store_true", help="skip git status (faster)")
    r.add_argument("--json", action="store_true")
    r = repo.add_parser("show", help="one repository: remotes, upstream, problems and suggested commands")
    r.add_argument("path")
    r.add_argument("--json", action="store_true")
    r = repo.add_parser("diff", help="changed files, incoming/outgoing commits, or the diff of one FILE")
    r.add_argument("path")
    r.add_argument("file", nargs="?")
    r.add_argument("--json", action="store_true")
    r = repo.add_parser("open", help="open the repository in the IDE, a terminal or the file manager")
    r.add_argument("path")
    r.add_argument("target", choices=("ide", "terminal", "files"))
    r = repo.add_parser("add", help="clone a repository into an installation (--url, --dest) or register one (--path)")
    r.add_argument("installation", nargs="?", help="installation root, e.g. /opt/odoo19")
    src = r.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="SSH or HTTPS Git URL; no passwords or tokens in it")
    src.add_argument("--path", help="an existing local repository")
    r.add_argument("--dest", help="folder relative to the installation root, e.g. custom/cms/admissions")
    r.add_argument("--ref", help="branch, tag or commit to check out")
    r.add_argument("--full", action="store_true", help="full history instead of a shallow clone")
    r.add_argument("--purpose", choices=("community", "enterprise", "themes", "custom", "other"))
    r.add_argument("--group", help="project group label")
    r.add_argument("--preferred-branch")
    r.add_argument("--no-addons", action="store_true", help="does not contribute to addons_path")
    r.add_argument("--no-bulk", action="store_true", help="leave out of bulk fetch/pull")
    r = repo.add_parser("apply", help="clone the repositories of a profile into an installation (kept ones are only recorded)")
    r.add_argument("installation")
    r.add_argument("--profile", required=True)
    r = repo.add_parser("addons", help="what repositories would add to each config's addons_path; --apply CONFIG writes it")
    r.add_argument("installation")
    r.add_argument("repos", nargs="+")
    r.add_argument("--apply", action="append", default=[], metavar="CONFIG", help="update this config (repeatable)")
    r.add_argument("--yes", action="store_true")
    r.add_argument("--json", action="store_true")
    r = repo.add_parser("forget", help="remove a repository (or one association) from the list; files stay")
    r.add_argument("path")
    r.add_argument("--installation")
    for name, text in (("fetch", "git fetch --prune"), ("pull", "git pull --ff-only; skips dirty, diverged, detached")):
        r = repo.add_parser(name, help=text)
        r.add_argument("paths", nargs="*")
        r.add_argument("--bulk", action="store_true", help="every repository selected for bulk operations")
        r.add_argument("--installation", help="with --bulk: only this installation")
    r = repo.add_parser("switch", help="switch to a branch; refuses uncommitted changes")
    r.add_argument("path")
    r.add_argument("branch")
    r.add_argument("--fetch-branch", action="store_true", help="fetch the branch first (single-branch clones)")
    r = repo.add_parser("checkout", help="detached checkout of a tag or commit; needs --confirm REF")
    r.add_argument("path")
    r.add_argument("ref")
    r.add_argument("--confirm", help="type the ref again")
    for name in ("add", "fetch", "pull", "switch", "checkout", "apply"):
        p = repo.choices[name]
        p.add_argument("--plan", action="store_true", help="dry run: checks and exact commands")
        p.add_argument("--yes", action="store_true")
        p.add_argument("--json", action="store_true")

    prof = sub.add_parser("profile", help="installation profiles and repository bundles: list, show, import, export, delete, org").add_subparsers(
        dest="profile_command", required=True)
    p = prof.add_parser("list", help="saved profiles and the org overlay")
    p.add_argument("--json", action="store_true")
    p = prof.add_parser("show", help="a profile merged with the layers below it, for one Odoo version")
    p.add_argument("name")
    p.add_argument("--version", "-V", dest="odoo_version", type=int)
    p.add_argument("--json", action="store_true")
    p = prof.add_parser("import", help="check a profile file and copy it into the profiles folder")
    p.add_argument("file")
    p.add_argument("--name")
    p.add_argument("--overwrite", action="store_true")
    p = prof.add_parser("export", help="sanitized profile of an installation (stdout, or saved with --name)")
    p.add_argument("root")
    p.add_argument("--name")
    p.add_argument("--overwrite", action="store_true")
    p = prof.add_parser("delete", help="move a saved profile aside (.trash-NAME-TIME.toml)")
    p.add_argument("name")
    p = prof.add_parser("org", help="show or set the org overlay file")
    p.add_argument("path", nargs="?")
    p.add_argument("--default", action="store_true", help="back to ~/.config/odoo-dev-panel/org.toml")

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

    rp = repair.add_parser("config-perms", help="give the configs of an installation the standard owner, group and mode")
    rp.add_argument("root", help="installation root, e.g. /opt/odoo17")
    rp.add_argument("--plan", action="store_true", help="dry run: show every change and the root script")
    rp.add_argument("--yes", "-y", action="store_true", help="do not ask for confirmation")

    config = sub.add_parser("config", help="show, check, edit or copy a discovered Odoo config").add_subparsers(
        dest="config_command", required=True)
    for name, help_ in (("show", "print the config, passwords masked"), ("check", "validate the config"),
                        ("edit", "edit in $EDITOR, validate, save with a backup"), ("copy", "new config next to it"),
                        ("set", "set or remove options, validate, save with a backup")):
        c = config.add_parser(name, help=help_)
        c.add_argument("path", help="config file")
        if name == "show":
            c.add_argument("--reveal", action="store_true", help="show the passwords too")
        if name == "copy":
            c.add_argument("name", help="new config name, e.g. client_b")
        if name == "set":
            c.add_argument("assignments", nargs="*", metavar="KEY=VALUE", help="e.g. http_port=8070 workers=2")
            c.add_argument("--unset", action="append", default=[], metavar="KEY",
                           help="remove the option, so Odoo uses its default (repeatable)")

    cmp_ = sub.add_parser("compare", help="how two configs differ: Odoo version and commit, Python, packages, addons_path, options")
    cmp_.add_argument("a", help="first config file")
    cmp_.add_argument("b", help="second config file")
    cmp_.add_argument("--json", action="store_true")

    dk = sub.add_parser("docker", help="Odoo containers: list (default), start, stop, restart, upgrade, logs, shell")
    dk.add_argument("--json", action="store_true")
    dsub = dk.add_subparsers(dest="docker_command")
    dsub.add_parser("list", help="Odoo containers: image, version, compose project, ports, config and addons mounts (read-only)")
    for name, help_ in (("start", "start a stopped container"), ("stop", "stop a container (clean shutdown, up to 30 s)"),
                        ("restart", "restart a container")):
        d = dsub.add_parser(name, help=help_)
        d.add_argument("container")
        d.add_argument("--plan", action="store_true", help="dry run: checks and steps. Changes nothing")
        d.add_argument("--json", action="store_true", help="with --plan: print the plan as JSON")
    d = dsub.add_parser("new", help="create an Odoo stack in Docker (Odoo + PostgreSQL) in ~/odp-docker/NAME")
    d.add_argument("name")
    d.add_argument("--version", "-V", default="18.0", help="Odoo version, 15.0 to 20.0 (default 18.0)")
    d.add_argument("--port", type=int, help="host port on localhost (default: the first free one from 18069)")
    d.add_argument("--addons", help="absolute path of your addons folder to mount (default: a new empty folder)")
    d.add_argument("--plan", action="store_true", help="dry run: checks and steps. Changes nothing")
    d.add_argument("--json", action="store_true", help="with --plan: print the plan as JSON")
    d = dsub.add_parser("delete", help="delete a stack made by `odp docker new`: containers, volumes and folder")
    d.add_argument("container")
    d.add_argument("--confirm", help="type the stack name to confirm")
    d.add_argument("--plan", action="store_true", help="dry run: checks and steps. Changes nothing")
    d.add_argument("--json", action="store_true", help="with --plan: print the plan as JSON")
    d = dsub.add_parser("upgrade", help="update (-u) or install (-i) modules in a database, inside the container")
    d.add_argument("container")
    d.add_argument("--db", "-d", required=True)
    d.add_argument("--update", "-u", action="append", default=[], help="module to update (repeatable, or comma separated)")
    d.add_argument("--install", "-i", action="append", default=[], help="module to install (repeatable, or comma separated)")
    d.add_argument("--no-demo", action="store_true", help="a new database without demo data")
    d.add_argument("--plan", action="store_true", help="dry run: checks and steps. Changes nothing")
    d.add_argument("--json", action="store_true", help="with --plan: print the plan as JSON")
    d = dsub.add_parser("logs", help="the container's log")
    d.add_argument("container")
    d.add_argument("--tail", type=int, default=200, help="last N lines (default 200)")
    d.add_argument("--follow", "-f", action="store_true")
    d.add_argument("--problems", "-p", action="store_true", help="print problems grouped by fingerprint instead of the log")
    d = dsub.add_parser("shell", help="odoo shell in a running container (interactive)")
    d.add_argument("container")
    d.add_argument("database")
    mods = sub.add_parser("modules", help="module dependencies from the manifests in a config's addons_path")
    mods.add_argument("config", help="config file")
    mods.add_argument("module", nargs="?", help="focus on one module: what it needs and what needs it")
    mods.add_argument("--depth", type=int, help="levels to follow with a focus (default: all)")
    mods.add_argument("--json", action="store_true")

    db = sub.add_parser("db", help="databases of an installation: list, backup, restore, clone, drop, neutralize, snapshots").add_subparsers(
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
    a = db_action("snapshot", "back up a database into the snapshot folder and keep only the newest snapshots of it")
    a.add_argument("source")
    a.add_argument("--keep", type=int, help="how many snapshots of this database to keep (default 3)")
    a = db_action("revert", "replace a database with a snapshot; the current one is kept under a new name")
    a.add_argument("database")
    a.add_argument("backup", metavar="SNAPSHOT", help="absolute path of a snapshot folder")
    a.add_argument("--confirm", help="type the database name to confirm")
    a = db_action("forget", "remove one snapshot folder")
    a.add_argument("backup", metavar="SNAPSHOT", help="absolute path of a snapshot folder")
    ds = db.add_parser("snapshots", help="snapshots of the installation, newest first")
    ds.add_argument("root", help="installation root, e.g. /opt/odoo17")
    ds.add_argument("database", nargs="?")
    ds.add_argument("--json", action="store_true")

    provision = sub.add_parser("provision", help="create a new Odoo installation").add_subparsers(
        dest="provision_command", required=True
    )
    def spec_args(target):
        target.add_argument("--version", "-V", dest="odoo_version", type=int, help="Odoo major version, e.g. 17 (optional with a profile that pins it)")
        target.add_argument("--profile", help="saved profile (~/.config/odoo-dev-panel/profiles/NAME.toml); flags override it")
        target.add_argument("--dest", action="append", default=[], metavar="NAME=FOLDER",
                            help="put repository NAME in FOLDER under the root (nested folders kept), repeatable")
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
        target.add_argument("--config-name", help="first config file name (default: default)")
        target.add_argument("--pg-host", help="PostgreSQL host (default: localhost)")
        target.add_argument("--pg-port", type=int, help="PostgreSQL port (default: 5432)")
        target.add_argument("--http-port", type=int)


    plan = provision.add_parser("plan", help="dry run: preflight checks, steps and the root script. Changes nothing")
    spec_args(plan)
    run_p = provision.add_parser("run", help="create the installation (asks for sudo once)")
    spec_args(run_p)
    run_p.add_argument("--yes", "-y", action="store_true", help="do not ask for confirmation")
    plan.add_argument("--script-only", action="store_true", help="print only the root script")
    plan.add_argument("--no-remote", action="store_true", help="skip git ls-remote checks of branches and access")

    sub.add_parser("sidecar", help="JSON-RPC server on stdio for the desktop app")
    sub.add_parser("askpass", help="SUDO_ASKPASS helper")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    # askpass receives a free-form prompt from sudo: bypass argparse.
    if raw[:1] == ["askpass"]:
        from . import askpass

        return askpass.main(raw[1:])
    if raw[:1] == ["modules"] and len(raw) > 1 and raw[1] in MODULE_VERBS:
        return cmd_module_verb(raw[1:])
    args = build_parser().parse_args(raw)

    if args.command == "sidecar":
        from . import sidecar

        # Added to odoo-dev but not logged in again: take the group through sg instead of asking for a logout.
        again = privilege.sg_reexec_argv([*paths.odp_command(), "sidecar"])
        if again:
            os.execv(again[0], again)
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
    if args.command == "profile":
        return cmd_profile(args)
    if args.command == "repo":
        if args.repo_command in ("add", "fetch", "pull") and getattr(args, "paths", True) == [] and not args.bulk:
            print("odp: give repository paths or --bulk", file=sys.stderr)
            return 2
        if args.repo_command == "add" and args.url and not (args.installation and args.dest):
            print("odp: --url needs an installation and --dest", file=sys.stderr)
            return 2
        return cmd_repo(args)
    if args.command == "services":
        return cmd_services(args)
    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "repair":
        return cmd_repair_perms(args) if args.repair_command == "config-perms" else cmd_repair_venv(args)
    if args.command == "config":
        return cmd_config(args)
    if args.command == "compare":
        return cmd_compare(args)
    if args.command == "modules":
        return cmd_modules(args)
    if args.command == "docker":
        return cmd_docker(args)
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
