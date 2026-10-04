"""GUI-facing server. The Tauri app spawns ``odp sidecar`` and talks JSON-RPC on its stdin/stdout.

The sidecar is only a client of the agents. It may die with the app at any time;
no Odoo process depends on it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import pwd
import sys
import tempfile

from . import __version__, client, paths, privilege, rpc

_logger = logging.getLogger(__name__)

FORWARDED_EVENTS = ("session.output", "session.ended", "session.state")


class Sidecar:
    def __init__(self) -> None:
        self.ui: rpc.Connection | None = None
        self.agents: dict[str, rpc.Connection] = {}
        self._agent_lock = asyncio.Lock()
        self._askpass_server: asyncio.AbstractServer | None = None
        self.askpass_path = os.path.join(
            os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(), f"odp-askpass-{os.getpid()}.sock"
        )
        self._unlocking: str | None = None
        self._purpose = "unlock"
        self._jobs: dict[str, asyncio.Task] = {}  # one provision and one repair at a time
        self._owners: tuple[float, set[str]] | None = None

    # -- agent connections ------------------------------------------------

    async def agent(self, user: str) -> rpc.Connection:
        async with self._agent_lock:
            conn = self.agents.get(user)
            if conn and not conn.closed.is_set():
                return conn
            handlers = {event: self._forwarder(user, event) for event in FORWARDED_EVENTS}
            handlers["agent.refused"] = self._forwarder(user, "agent.refused")
            conn = await client.connect(user, handlers)
            conn.on_close(lambda: self._on_agent_closed(user, conn))
            await conn.request("session.watch", {"enabled": True})
            self.agents[user] = conn
            return conn

    def _forwarder(self, user: str, event: str):
        async def forward(params, _conn):
            if self.ui:
                await self.ui.notify(event, {"user": user, **(params or {})})

        return forward

    def _on_agent_closed(self, user: str, conn: rpc.Connection) -> None:
        if self.agents.get(user) is conn:
            del self.agents[user]
        if self.ui and not self.ui.closed.is_set():
            self.ui.spawn(self.ui.notify("agent.disconnected", {"user": user}))

    # -- askpass ----------------------------------------------------------

    async def _start_askpass_server(self) -> None:
        try:
            os.unlink(self.askpass_path)
        except FileNotFoundError:
            pass
        old_umask = os.umask(0o177)
        try:
            self._askpass_server = await asyncio.start_unix_server(self._on_askpass, path=self.askpass_path)
        finally:
            os.umask(old_umask)

    async def _on_askpass(self, reader, writer) -> None:
        conn = rpc.Connection(reader, writer, {"askpass": self._h_askpass}, name="askpass")
        await conn.serve()

    async def _h_askpass(self, params, _conn):
        if not self.ui:
            return {"password": None}
        answer = await self.ui.request(
            "ui.askPassword",
            {"prompt": (params or {}).get("prompt", ""), "user": self._unlocking, "purpose": self._purpose}, timeout=300
        )
        return {"password": (answer or {}).get("password")}

    # -- UI handlers ------------------------------------------------------

    def handlers(self):
        return {
            "app.info": self.h_app_info,
            "agents.list": self.h_agents_list,
            "agent.start": self.h_agent_start,
            "agent.stop": self.h_agent_stop,
            "agent.enable": self.h_agent_enable,
            "sessions.list": self.h_sessions_list,
            "session.start": self.h_session_start,
            "run.start": self.h_run_start,
            "run.open": self.h_run_open,
            "session.stop": self.h_session_stop,
            "session.follow": self.h_session_follow,
            "session.unfollow": self.h_session_unfollow,
            "session.write": self.h_session_write,
            "session.resize": self.h_session_resize,
            "session.problems": self.h_session_problems,
            "provision.plan": self.h_provision_plan,
            "provision.run": self.h_provision_run,
            "doctor.run": self.h_doctor,
            "repair.plan": self.h_repair_plan,
            "repair.run": self.h_repair_run,
            "perms.plan": self.h_perms_plan,
            "perms.run": self.h_perms_run,
            "config.open": self.h_config_open,
            "config.validate": self.h_config_validate,
            "config.form": self.h_config_form,
            "config.save": self.h_config_save,
            "config.copy": self.h_config_copy,
            "db.list": self.h_db_list,
            "db.plan": self.h_db_plan,
            "db.run": self.h_db_run,
            "discover.scan": self.h_discover,
            "discover.adopt": self.h_adopt,
            "group.join": self.h_group_join,
            "debug.print": self.h_debug_print,
        }

    async def h_app_info(self, params, _conn):
        return {
            "version": __version__,
            "user": pwd.getpwuid(os.getuid()).pw_name,
            "socket_dir": str(paths.socket_dir()),
            "pid": os.getpid(),
            "group": privilege.group_state(),
        }

    async def h_group_join(self, params, _conn):
        """Add the developer to odoo-dev (one sudo prompt). The app then restarts its sidecar, which takes the
        group through sg, so no logout is needed."""
        state = privilege.group_state()
        if state["member"]:
            return state
        if not state["exists"]:
            raise rpc.RpcError(rpc.CONFLICT, f"group {state['group']} does not exist: reinstall the package")
        self._unlocking, self._purpose = pwd.getpwuid(os.getuid()).pw_name, "join"
        try:
            code, output = await privilege.join_group_askpass({"ODP_ASKPASS_SOCK": self.askpass_path})
        finally:
            self._unlocking, self._purpose = None, "unlock"
        if code != 0:
            raise rpc.RpcError(rpc.FORBIDDEN, f"could not add you to {state['group']}: {output or f'exit {code}'}")
        return privilege.group_state()

    async def _install_owners(self) -> set[str]:
        """Run-as users of discovered installations, cached for a minute (agents.list is polled)."""
        from .discover.installs import scan_installations

        now = asyncio.get_running_loop().time()
        if self._owners is None or now - self._owners[0] > 60:
            installs = await asyncio.to_thread(scan_installations)
            self._owners = (now, {i.owner for i in installs if i.owner and i.venv_ok is not None})
        return self._owners[1]

    async def h_agents_list(self, params, _conn):
        owners = await self._install_owners()
        members = client.group_members()
        users = client.candidate_users(extra=owners)
        statuses = await asyncio.gather(*(client.agent_status(u) for u in users))
        return [{**s, "enabled": s["user"] in members} for s in statuses]

    async def h_agent_enable(self, params, _conn):
        """Add a discovered run-as user to the odoo-dev group (one sudo call, shown to the user first)."""
        user = _user(params)
        if user not in await self._install_owners():
            raise rpc.RpcError(rpc.INVALID_PARAMS, f"{user} is not the run-as user of a discovered installation")
        if user in client.group_members():
            return {"user": user, "enabled": True}
        self._unlocking, self._purpose = user, "enable"
        try:
            code, output = await privilege.enable_user_askpass(user, {"ODP_ASKPASS_SOCK": self.askpass_path})
        finally:
            self._unlocking, self._purpose = None, "unlock"
        if code != 0:
            raise rpc.RpcError(rpc.FORBIDDEN, f"could not add {user} to {paths.DEFAULT_GROUP}: {output or f'exit {code}'}")
        return {"user": user, "enabled": True}

    async def h_agent_start(self, params, _conn):
        user = _user(params)
        status = await client.agent_status(user)
        if status["state"] == "running":
            return status
        if user not in client.group_members():
            raise rpc.RpcError(rpc.CONFLICT, f"{user} is not in {paths.DEFAULT_GROUP}; enable it first")
        self._unlocking = user
        try:
            code, output = await privilege.start_agent_askpass(user, {"ODP_ASKPASS_SOCK": self.askpass_path})
        finally:
            self._unlocking = None
        if code != 0:
            raise rpc.RpcError(rpc.FORBIDDEN, f"could not start agent for {user}: {output or f'exit {code}'}")
        return await client.wait_running(user)

    async def h_agent_stop(self, params, _conn):
        user = _user(params)
        conn = await self.agent(user)
        return await conn.request("agent.shutdown")

    async def h_sessions_list(self, params, _conn):
        result = []
        for user, path in client.agent_sockets().items():
            try:
                conn = await self.agent(user)
                sessions = await conn.request("session.list", timeout=5)
            except (rpc.RpcError, rpc.ConnectionClosed, asyncio.TimeoutError) as exc:
                _logger.info("agent %s unavailable: %s", user, exc)
                continue
            result.extend(sessions)
        result.sort(key=lambda s: s.get("started_at", ""), reverse=True)
        return result

    async def h_session_start(self, params, _conn):
        params = dict(params or {})
        conn = await self.agent(_user(params))
        params.pop("user")
        return await conn.request("session.start", params)

    async def h_run_start(self, params, _conn):
        """Start a discovered instance as its run-as user. ``params``: instance plus RunSpec fields."""
        from . import run
        from .discover import scan

        params = dict(params or {})
        ref = params.pop("instance", None)
        if not isinstance(ref, str) or not ref:
            raise rpc.RpcError(rpc.INVALID_PARAMS, "instance is required")
        snap = await asyncio.to_thread(scan.scan, None, False)
        busy = {p["port"] for p in snap["processes"] if p.get("port")}
        planned = run.plan(snap, ref, params, busy | set(await asyncio.to_thread(_listening)))
        conn = await self.agent(planned.pop("user"))
        return await conn.request("session.start", planned)

    async def h_run_open(self, params, _conn):
        """Open a running instance in the default browser. Only http://localhost:<port> is accepted."""
        import subprocess

        port = (params or {}).get("port")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise rpc.RpcError(rpc.INVALID_PARAMS, "port is required")
        subprocess.Popen(
            ["xdg-open", f"http://localhost:{port}"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        return True

    async def h_session_stop(self, params, _conn):
        conn = await self.agent(_user(params))
        return await conn.request("session.stop", {"id": params.get("id"), "timeout": params.get("timeout", 15)}, timeout=30)

    async def h_session_follow(self, params, _conn):
        conn = await self.agent(_user(params))
        return await conn.request("session.follow", {"id": params.get("id"), "offset": params.get("offset", 0)})

    async def h_session_unfollow(self, params, _conn):
        conn = await self.agent(_user(params))
        return await conn.request("session.unfollow", {"id": params.get("id")})

    async def h_session_write(self, params, _conn):
        conn = await self.agent(_user(params))
        return await conn.request("session.write", {"id": params.get("id"), "data": params.get("data")})

    async def h_session_resize(self, params, _conn):
        conn = await self.agent(_user(params))
        return await conn.request("session.resize", {"id": params.get("id"), "rows": params.get("rows"), "cols": params.get("cols")})

    async def h_session_problems(self, params, _conn):
        """Counts per level and grouped warnings/errors of a session log (its last 8 MB)."""
        from . import logs

        conn = await self.agent(_user(params))
        text, start = await logs.read_tail(conn, params.get("id"))
        minimum = params.get("level") or logs.PROBLEM
        if minimum not in logs.LEVELS:
            raise rpc.RpcError(rpc.INVALID_PARAMS, f"unknown level {minimum!r}")
        return {**logs.analyze(text, minimum), "start_offset": start}

    async def h_provision_plan(self, params, _conn):
        """Dry run: preflight, steps, root script (verifier only) and config with placeholder passwords."""
        from dataclasses import asdict

        from .provision import plan as plan_mod
        from .provision import preflight
        from .provision.spec import SpecError, spec_from_dict

        try:
            spec = spec_from_dict(params or {})
        except SpecError as exc:
            raise rpc.RpcError(rpc.INVALID_PARAMS, str(exc)) from exc
        sec = plan_mod.Secrets.generate()
        checks = await asyncio.to_thread(preflight.run_preflight, spec)
        placeholder = plan_mod.Secrets("<generated at run time>", "<generated at run time>")
        return {
            "spec": asdict(spec),
            "preflight": [asdict(c) for c in checks],
            "ok": not preflight.has_failures(checks),
            "steps": [asdict(s) for s in plan_mod.build_steps(spec, sec)],
            "root_script": plan_mod.render_root_script(spec, sec),
            "config": plan_mod.render_conf(spec, placeholder),
        }

    async def h_provision_run(self, params, _conn):
        """Start a provision in the background. Progress arrives as provision.step, the end as provision.finished."""
        from .provision import execute
        from .provision.spec import SpecError, spec_from_dict

        try:
            spec = spec_from_dict(params or {})
        except SpecError as exc:
            raise rpc.RpcError(rpc.INVALID_PARAMS, str(exc)) from exc

        async def root_runner(script: str, rep) -> int:
            self._unlocking, self._purpose = spec.run_as, "provision"
            try:
                return await execute.sudo_runner(script, rep, {"ODP_ASKPASS_SOCK": self.askpass_path})
            finally:
                self._unlocking, self._purpose = None, "unlock"

        async def work(report) -> dict:
            await execute.provision(spec, report, root_runner=root_runner)
            return {}

        run_id = self._start_job("provision", work, {"root": spec.root, "run_as": spec.run_as, "conf_path": spec.conf_path})
        return {"run_id": run_id, "run_as": spec.run_as, "root": spec.root}

    def _start_job(self, kind: str, work, finished: dict) -> str:
        """Run ``work(report)`` in the background. Progress goes out as ``<kind>.step``, the end as ``<kind>.finished``
        with ``finished`` plus ok/error and what ``work`` returned."""
        task = self._jobs.get(kind)
        if task and not task.done():
            raise rpc.RpcError(rpc.CONFLICT, f"a {kind} is already running")
        run_id = os.urandom(4).hex()
        ui = self.ui
        # One queue and one sender keep the events in order and put <kind>.finished after the last step.
        queue: asyncio.Queue = asyncio.Queue()

        def report(event: dict) -> None:
            queue.put_nowait((f"{kind}.step", {"run_id": run_id, **event}))

        async def sender() -> None:
            while True:
                item = await queue.get()
                if item is None:
                    return
                if ui and not ui.closed.is_set():
                    await ui.notify(*item)

        async def job() -> None:
            error, result = None, {}
            send = asyncio.create_task(sender())
            try:
                result = await work(report) or {}
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
                _logger.info("%s %s failed: %s", kind, run_id, exc)
            queue.put_nowait((f"{kind}.finished", {"run_id": run_id, "ok": error is None, "error": error, **finished, **result}))
            queue.put_nowait(None)
            await send

        self._jobs[kind] = asyncio.create_task(job())
        return run_id

    async def h_doctor(self, params, _conn):
        from . import doctor

        return await asyncio.to_thread(doctor.run, None, None, not (params or {}).get("no_databases"))

    async def _repair_plan(self, params):
        from .discover import scan
        from .doctor import repair

        params = params or {}
        root = params.get("root")
        if not isinstance(root, str) or not root:
            raise rpc.RpcError(rpc.INVALID_PARAMS, "root is required")
        python = params.get("python") or None
        if python is not None and not isinstance(python, str):
            raise rpc.RpcError(rpc.INVALID_PARAMS, "python must be a string like 3.12")
        snap = await asyncio.to_thread(scan.scan, None, False)
        inst = next((i for i in snap["installations"] if i["root"] == os.path.normpath(root)), None)
        if inst is None:
            raise rpc.RpcError(rpc.INVALID_PARAMS, f"{root} is not a discovered Odoo installation")
        agent = await client.agent_status(inst["owner"]) if inst.get("owner") else {"state": "stopped"}
        return await asyncio.to_thread(
            repair.plan_venv_repair, inst, snap["processes"], python, params.get("custom", True) is not False,
            bool(params.get("carry_extras")), agent["state"] == "running",
        )

    async def h_repair_plan(self, params, _conn):
        """Dry run of the venv rebuild: checks, requirement files, packages not carried over, steps."""
        return (await self._repair_plan(params)).as_dict()

    async def h_repair_run(self, params, _conn):
        """Rebuild a venv in the background. Progress arrives as repair.step, the end as repair.finished."""
        from .doctor import repair

        plan = await self._repair_plan(params)
        if not plan.ok:
            raise rpc.RpcError(rpc.CONFLICT, "; ".join(c.detail for c in plan.checks if c.status == "fail"))

        async def work(report) -> dict:
            return await repair.repair_venv(plan, report)

        run_id = self._start_job("repair", work, {"root": plan.root, "venv": plan.venv})
        return {"run_id": run_id, "root": plan.root}

    async def _perms_plan(self, params):
        from .discover import scan
        from .doctor import permissions

        root = (params or {}).get("root")
        if not isinstance(root, str) or not root:
            raise rpc.RpcError(rpc.INVALID_PARAMS, "root is required")
        snap = await asyncio.to_thread(scan.scan, None, False)
        try:
            return permissions.plan_config_perms(snap, root)
        except permissions.PermissionsError as exc:
            raise rpc.RpcError(rpc.INVALID_PARAMS, str(exc)) from exc

    async def h_perms_plan(self, params, _conn):
        """Dry run of the config permission repair: every path with its current and target owner/group/mode."""
        return (await self._perms_plan(params)).as_dict()

    async def h_perms_run(self, params, _conn):
        """Apply the standard config permissions with one sudo call. Progress as repair.step, end as repair.finished."""
        from .doctor import permissions
        from .provision import execute

        plan = await self._perms_plan(params)

        async def root_runner(script: str, rep) -> int:
            self._unlocking, self._purpose = plan.run_as, "permissions"
            try:
                return await execute.sudo_runner(script, rep, {"ODP_ASKPASS_SOCK": self.askpass_path})
            finally:
                self._unlocking, self._purpose = None, "unlock"

        async def work(report) -> dict:
            return await permissions.apply_config_perms(plan, report, root_runner)

        run_id = self._start_job("repair", work, {"root": plan.root, "repair": "config-perms"})
        return {"run_id": run_id, "root": plan.root}

    async def _config(self, params) -> tuple[str, dict]:
        """Only configs that discovery found can be opened, saved or copied."""
        from .discover import scan

        path = (params or {}).get("path")
        if not isinstance(path, str) or not path:
            raise rpc.RpcError(rpc.INVALID_PARAMS, "path is required")
        snap = await asyncio.to_thread(scan.scan, None, False)
        if not any(i["path"] == path for i in snap["instances"]):
            raise rpc.RpcError(rpc.INVALID_PARAMS, f"{path} is not a discovered Odoo config")
        return path, snap

    async def h_config_open(self, params, _conn):
        """Config text with secrets masked (``reveal``: plain), its sha256, access and validation issues."""
        from . import configedit

        path, snap = await self._config(params)
        try:
            return (await asyncio.to_thread(configedit.open_config, path, bool((params or {}).get("reveal")), snap)).as_dict()
        except configedit.ConfigError as exc:
            raise rpc.RpcError(rpc.INVALID_PARAMS, str(exc)) from exc

    async def h_config_validate(self, params, _conn):
        from dataclasses import asdict

        from . import configedit

        path, snap = await self._config(params)
        text = (params or {}).get("text")
        if not isinstance(text, str):
            raise rpc.RpcError(rpc.INVALID_PARAMS, "text is required")
        return [asdict(i) for i in configedit.validate(text, snap, path)]

    async def h_config_form(self, params, _conn):
        """Form view data for ``text`` (masked or not) after the optional ``changes`` ({key: value or null})."""
        from . import configedit

        path, snap = await self._config(params)
        text, changes = (params or {}).get("text"), (params or {}).get("changes") or {}
        if not isinstance(text, str) or not isinstance(changes, dict) or \
                not all(isinstance(v, str) or v is None for v in changes.values()):
            raise rpc.RpcError(rpc.INVALID_PARAMS, "text is required; changes maps option names to text or null")
        try:
            return await asyncio.to_thread(configedit.form, text, snap, path, changes)
        except configedit.ConfigError as exc:
            raise rpc.RpcError(rpc.INVALID_PARAMS, str(exc)) from exc

    async def h_config_save(self, params, _conn):
        from . import configedit

        path, snap = await self._config(params)
        text, base = (params or {}).get("text"), (params or {}).get("sha")
        if not isinstance(text, str) or not isinstance(base, str):
            raise rpc.RpcError(rpc.INVALID_PARAMS, "text and sha are required")
        try:
            return await asyncio.to_thread(configedit.save, path, text, base, snap)
        except configedit.ConfigError as exc:
            raise rpc.RpcError(rpc.CONFLICT, str(exc)) from exc

    async def h_config_copy(self, params, _conn):
        from . import configedit

        path, snap = await self._config(params)
        name = (params or {}).get("name")
        if not isinstance(name, str) or not name:
            raise rpc.RpcError(rpc.INVALID_PARAMS, "name is required")
        inst = next((i for i in snap["instances"] if i["path"] == path), {})
        owner = next((i.get("owner") for i in snap["installations"] if i["root"] == inst.get("installation")), None)
        try:
            return await asyncio.to_thread(configedit.copy, path, name, owner == pwd.getpwuid(os.getuid()).pw_name,
                                           configedit.copy_folder(path, inst.get("installation")))
        except configedit.ConfigError as exc:
            raise rpc.RpcError(rpc.CONFLICT, str(exc)) from exc

    async def _db_prepare(self, params):
        from .database import context

        params = params or {}
        root = params.get("root")
        if not isinstance(root, str) or not root:
            raise rpc.RpcError(rpc.INVALID_PARAMS, "root is required")
        try:
            return await context.prepare(root)
        except context.NotFound as exc:
            raise rpc.RpcError(rpc.INVALID_PARAMS, str(exc)) from exc

    async def h_db_list(self, params, _conn):
        """Databases of one installation's role, plus the recipes that can be run on them."""
        from .database import recipes

        _inst, ctx = await self._db_prepare(params)
        return {"databases": ctx.databases, "error": getattr(ctx, "listing_error", None),
                "recipes": await asyncio.to_thread(recipes.available), "agent_running": ctx.agent_running}

    async def _db_plan(self, params):
        from .database import ops

        inst, ctx = await self._db_prepare(params)
        fields = {k: params.get(k) or None for k in ("source", "target", "backup", "dest", "recipe", "confirm")}
        for key, value in fields.items():
            if value is not None and not isinstance(value, str):
                raise rpc.RpcError(rpc.INVALID_PARAMS, f"{key} must be a string")
        kind = params.get("action")
        if kind not in ops.KINDS:
            raise rpc.RpcError(rpc.INVALID_PARAMS, f"action must be one of {', '.join(ops.KINDS)}")
        return await asyncio.to_thread(ops.plan_db, kind, inst, ctx, **fields)

    async def h_db_plan(self, params, _conn):
        """Dry run of a database action: checks, steps. Changes nothing. The password never leaves the sidecar."""
        return (await self._db_plan(params)).as_dict()

    async def h_db_run(self, params, _conn):
        """Run a database action in the background. Progress arrives as db.step, the end as db.finished."""
        from .database import ops

        plan = await self._db_plan(params)
        if not plan.ok:
            raise rpc.RpcError(rpc.CONFLICT, "; ".join(c.detail for c in plan.checks if c.status == "fail"))

        async def work(report) -> dict:
            return await ops.run_db(plan, report)

        run_id = self._start_job("db", work, {"root": plan.root, "action": plan.kind, "source": plan.source, "target": plan.target})
        return {"run_id": run_id, "root": plan.root}

    async def h_discover(self, params, _conn):
        from pathlib import Path

        from .discover import scan

        roots = [Path(r) for r in (params or {}).get("roots") or []] or None
        return await asyncio.to_thread(scan.scan, roots, not (params or {}).get("no_databases"))

    async def h_adopt(self, params, _conn):
        """Adopt or release (``adopt: false``) a discovered installation. Writes the registry only."""
        from .discover import registry, scan

        root = (params or {}).get("root")
        if not isinstance(root, str) or not root:
            raise rpc.RpcError(rpc.INVALID_PARAMS, "root is required")
        root = os.path.normpath(root)
        try:
            if (params or {}).get("adopt", True):
                snap = await asyncio.to_thread(scan.scan, None, False)
                inst = next((i for i in snap["installations"] if i["root"] == root), None)
                if inst is None:
                    raise rpc.RpcError(rpc.INVALID_PARAMS, f"{root} is not a discovered Odoo installation")
                return registry.adopt(inst, (params or {}).get("name"))
            return {"released": registry.release(root)}
        except registry.RegistryError as exc:
            raise rpc.RpcError(rpc.INVALID_PARAMS, str(exc)) from exc

    async def h_debug_print(self, params, _conn):
        # Proves that stray prints cannot corrupt the RPC stream: stdout points to stderr.
        print((params or {}).get("text", "stray print"))
        return True

    # -- main loop --------------------------------------------------------

    async def run(self, rpc_in: int, rpc_out: int) -> None:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=rpc.MAX_FRAME)
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(rpc_in, "rb", 0))
        transport, protocol = await loop.connect_write_pipe(
            asyncio.streams.FlowControlMixin, os.fdopen(rpc_out, "wb", 0)
        )
        writer = asyncio.StreamWriter(transport, protocol, reader, loop)
        await self._start_askpass_server()
        self.ui = rpc.Connection(reader, writer, self.handlers(), name="ui")
        try:
            await self.ui.serve()
        finally:
            for conn in list(self.agents.values()):
                await conn.close()
            if self._askpass_server:
                self._askpass_server.close()
            try:
                os.unlink(self.askpass_path)
            except FileNotFoundError:
                pass


def _listening() -> list[int]:
    from .discover.ports import listening_ports

    return list(listening_ports())


def _user(params) -> str:
    user = (params or {}).get("user")
    if not isinstance(user, str) or not user:
        raise rpc.RpcError(rpc.INVALID_PARAMS, "user is required")
    try:
        pwd.getpwnam(user)
    except KeyError as exc:
        raise rpc.RpcError(rpc.INVALID_PARAMS, f"unknown user {user}") from exc
    return user


def main() -> int:
    # Keep a private copy of the real stdout for RPC, then send fd 1 to stderr,
    # so a print() anywhere in the process can never corrupt the RPC stream.
    rpc_out = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(asctime)s sidecar %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(Sidecar().run(0, rpc_out))
    return 0
