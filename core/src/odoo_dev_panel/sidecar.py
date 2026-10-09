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
        self._git_run: tuple[str, list[bool]] | None = None  # running repo job and its cancel flag

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
            "modules.graph": self.h_modules_graph,
            "compare.run": self.h_compare,
            "compare.databases": self.h_compare_databases,
            "compare.installations": self.h_compare_installations,
            "docker.list": self.h_docker_list,
            "docker.plan": self.h_docker_plan,
            "docker.run": self.h_docker_run,
            "docker.logs": self.h_docker_logs,
            "docker.new_plan": self.h_docker_new_plan,
            "docker.new_run": self.h_docker_new_run,
            "docker.delete_plan": self.h_docker_delete_plan,
            "docker.delete_run": self.h_docker_delete_run,
            "docker.shell": self.h_docker_shell,
            "config.copy": self.h_config_copy,
            "db.list": self.h_db_list,
            "db.plan": self.h_db_plan,
            "db.run": self.h_db_run,
            "db.snapshots": self.h_db_snapshots,
            "discover.scan": self.h_discover,
            "services.list": self.h_services_list,
            "services.action": self.h_services_action,
            "services.journal": self.h_services_journal,
            "services.show": self.h_services_show,
            "discover.adopt": self.h_adopt,
            "repo.list": self.h_repo_list,
            "repo.show": self.h_repo_show,
            "repo.diff": self.h_repo_diff,
            "repo.plan": self.h_repo_plan,
            "repo.run": self.h_repo_run,
            "repo.cancel": self.h_repo_cancel,
            "repo.register": self.h_repo_register,
            "repo.forget": self.h_repo_forget,
            "repo.open": self.h_repo_open,
            "desktop.pickFile": self.h_pick_file,
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

    # -- W1-W10: Git repositories -------------------------------------------

    async def _snap(self) -> dict:
        from .discover import scan

        return await asyncio.to_thread(scan.scan, None, False)

    @staticmethod
    def _git_error(exc: Exception) -> rpc.RpcError:
        from .git import api

        if isinstance(exc, (api.NotFound, LookupError)):
            return rpc.RpcError(rpc.NOT_FOUND, str(exc))
        return rpc.RpcError(rpc.INVALID_PARAMS, str(exc))

    async def h_repo_list(self, params, _conn):
        """Repositories of the discovered installations with live Git state. ``state: false`` skips git status."""
        from .git import registry, workspace

        params = params or {}
        inst = params.get("installation")
        if inst is not None and not isinstance(inst, str):
            raise rpc.RpcError(rpc.INVALID_PARAMS, "installation is a root path")
        try:
            return await asyncio.to_thread(workspace.listing, await self._snap(), None, inst, params.get("state", True) is not False)
        except registry.RegistryError as exc:
            raise rpc.RpcError(rpc.INTERNAL_ERROR, str(exc)) from exc

    async def h_repo_show(self, params, _conn):
        from .git import workspace

        path = (params or {}).get("path")
        if not isinstance(path, str):
            raise rpc.RpcError(rpc.INVALID_PARAMS, "path is required")
        try:
            return await asyncio.to_thread(workspace.show, path, await self._snap())
        except LookupError as exc:
            raise self._git_error(exc) from exc

    async def h_repo_diff(self, params, _conn):
        from .git import workspace

        path, file = (params or {}).get("path"), (params or {}).get("file")
        if not isinstance(path, str) or file is not None and not isinstance(file, str):
            raise rpc.RpcError(rpc.INVALID_PARAMS, "path is required; file is text")
        try:
            return await asyncio.to_thread(workspace.diff, path, await self._snap(), file)
        except LookupError as exc:
            raise self._git_error(exc) from exc

    async def _repo_plan(self, params):
        from .git import api

        params = params or {}
        try:
            return await asyncio.to_thread(api.plan, params.get("op"), params, await self._snap())
        except (api.ApiError, LookupError) as exc:
            raise self._git_error(exc) from exc

    async def h_repo_plan(self, params, _conn):
        """Dry run of fetch, pull, switch, checkout or clone: per-repository checks and the exact commands."""
        return (await self._repo_plan(params)).as_dict()

    async def h_repo_run(self, params, _conn):
        """Run a repository plan in the background, one repository at a time. Progress as git.step, the end as
        git.finished with a result per repository (ok, failed, skipped, cancelled)."""
        from .git import ops

        plan = await self._repo_plan(params)
        if not plan.ok:
            failed = [c["detail"] for c in plan.checks if c["status"] == "fail"] or ["nothing can run"]
            raise rpc.RpcError(rpc.CONFLICT, "; ".join(failed))
        flag = [False]

        async def work(report) -> dict:
            return await ops.run_plan(plan, report, lambda: flag[0])

        run_id = self._start_job("git", work, {"op": plan.op})
        self._git_run = (run_id, flag)
        return {"run_id": run_id, "op": plan.op, "repos": [i.repo for i in plan.items]}

    async def h_repo_cancel(self, params, _conn):
        """Stop after the repository that is running now. A running Git command is never killed."""
        run_id = (params or {}).get("run_id")
        if not self._git_run or self._git_run[0] != run_id:
            raise rpc.RpcError(rpc.NOT_FOUND, "no such repository job")
        self._git_run[1][0] = True
        return {"cancelling": True}

    async def h_repo_register(self, params, _conn):
        """Add an existing repository (and its association to an installation). Writes repositories.json only."""
        from .git import api

        try:
            return await asyncio.to_thread(api.register_existing, params or {}, await self._snap())
        except (api.ApiError, LookupError) as exc:
            raise self._git_error(exc) from exc

    async def h_repo_forget(self, params, _conn):
        from .git import api

        try:
            return {"forgotten": await asyncio.to_thread(api.forget, params or {})}
        except api.ApiError as exc:
            raise self._git_error(exc) from exc

    async def h_repo_open(self, params, _conn):
        from .git import opener, workspace

        params = params or {}
        path = params.get("path")
        if not isinstance(path, str):
            raise rpc.RpcError(rpc.INVALID_PARAMS, "path is required")
        try:
            row = await asyncio.to_thread(workspace.resolve, path, await self._snap())
            return {"argv": opener.open_path(row["path"], params.get("target", "ide"))}
        except LookupError as exc:
            raise self._git_error(exc) from exc
        except opener.OpenError as exc:
            raise rpc.RpcError(rpc.UNAVAILABLE, str(exc)) from exc

    async def h_pick_file(self, params, _conn):
        """Native file chooser as the developer. ``{path: null}`` when cancelled."""
        from . import desktop

        params = params or {}
        title, kind, start = params.get("title") or "Choose a file", params.get("kind") or "any", params.get("start") or ""
        if not all(isinstance(v, str) for v in (title, kind, start)):
            raise rpc.RpcError(rpc.INVALID_PARAMS, "title, kind and start are text")
        try:
            return {"path": await desktop.pick_file(title, kind, start)}
        except desktop.PickError as exc:
            raise rpc.RpcError(rpc.UNAVAILABLE, str(exc)) from exc

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

    async def h_modules_graph(self, params, _conn):
        """Module dependency graph of a config's addons_path; ``module`` and ``depth`` add a focus on one module."""
        from . import configedit, modules

        path, snap = await self._config(params)
        name, depth = (params or {}).get("module"), (params or {}).get("depth")
        extra, database = (params or {}).get("extra") or [], (params or {}).get("database") or None
        if name is not None and not isinstance(name, str) or depth is not None and not isinstance(depth, int):
            raise rpc.RpcError(rpc.INVALID_PARAMS, "module is text, depth is a number")
        if not isinstance(extra, list) or not all(isinstance(e, str) and e.startswith("/") for e in extra):
            raise rpc.RpcError(rpc.INVALID_PARAMS, "extra is a list of absolute folders")
        if database is not None and not isinstance(database, str):
            raise rpc.RpcError(rpc.INVALID_PARAMS, "database is text")
        try:
            full = await asyncio.to_thread(modules.for_config, path, snap, name, depth, extra)
        except KeyError as exc:
            raise rpc.RpcError(rpc.INVALID_PARAMS, f"no module {exc.args[0]} in the addons_path") from exc
        except (configedit.ConfigError, OSError) as exc:
            raise rpc.RpcError(rpc.INVALID_PARAMS, str(exc)) from exc
        full["db_error"] = None
        if database:
            from . import dbquery

            try:
                states = await dbquery.installed_modules(full["installation"] or "", database)
                modules.overlay(full, states, full.get("series"))
            except dbquery.QueryError as exc:
                full["db_error"] = str(exc)
        return full

    async def h_docker_list(self, params, _conn):
        """Odoo containers, read-only (docker ps and inspect). Its own call: a slow daemon does not slow the scan."""
        from . import dockerprov
        from .discover import docker

        result = await asyncio.to_thread(docker.discover_docker)
        for c in result["containers"]:
            found = dockerprov.stack_of(c)  # a stack this app made: it may delete it
            c["app_stack"] = found[0] if found else None
        return {**result, "versions": list(dockerprov.VERSIONS), "stacks_root": dockerprov.stacks_root()}

    async def _docker_new_plan(self, params):
        from . import dockerprov
        from .discover import docker

        params = params or {}
        port, addons = params.get("port"), params.get("addons") or None
        if port is not None and (not isinstance(port, int) or isinstance(port, bool)):
            raise rpc.RpcError(rpc.INVALID_PARAMS, "port must be a number")
        if addons is not None and not isinstance(addons, str):
            raise rpc.RpcError(rpc.INVALID_PARAMS, "addons must be a path")
        found = await asyncio.to_thread(docker.discover_docker)
        if found["error"]:
            raise rpc.RpcError(rpc.CONFLICT, found["error"])
        return await asyncio.to_thread(dockerprov.plan_new, str(params.get("name") or ""), str(params.get("version") or ""),
                                       found["containers"], port, addons)

    async def h_docker_new_plan(self, params, _conn):
        """Dry run of a new Odoo stack in Docker: checks (name, folder, port), steps. Changes nothing."""
        return (await self._docker_new_plan(params)).as_dict()

    async def h_docker_new_run(self, params, _conn):
        """Create the stack in the background. Progress arrives as docker.step, the end as docker.finished."""
        from . import dockerprov

        plan = await self._docker_new_plan(params)
        if not plan.ok:
            raise rpc.RpcError(rpc.CONFLICT, "; ".join(c.detail for c in plan.checks if c.status == "fail"))

        async def work(report) -> dict:
            return await dockerprov.run_new(plan, report)

        run_id = self._start_job("docker", work, {"action": "new", "name": plan.name})
        return {"run_id": run_id, "name": plan.name}

    async def _docker_delete_plan(self, params):
        from . import dockerops, dockerprov
        from .discover import docker

        params = params or {}
        found = await asyncio.to_thread(docker.discover_docker)
        if found["error"]:
            raise rpc.RpcError(rpc.CONFLICT, found["error"])
        try:
            return dockerprov.plan_delete(params.get("container"), found["containers"], params.get("confirm") or None)
        except dockerops.DockerError as exc:
            raise rpc.RpcError(rpc.INVALID_PARAMS, str(exc)) from exc

    async def h_docker_delete_plan(self, params, _conn):
        """Dry run of deleting a stack this app made. Needs the stack name typed. Changes nothing."""
        return (await self._docker_delete_plan(params)).as_dict()

    async def h_docker_delete_run(self, params, _conn):
        from . import dockerprov

        plan = await self._docker_delete_plan(params)
        if not plan.ok:
            raise rpc.RpcError(rpc.CONFLICT, "; ".join(c.detail for c in plan.checks if c.status == "fail"))

        async def work(report) -> dict:
            return await dockerprov.run_delete(plan, report)

        run_id = self._start_job("docker", work, {"action": "delete", "container": plan.container})
        return {"run_id": run_id, "name": plan.name}

    async def _docker_target(self, params):
        from . import dockerops
        from .discover import docker

        name = (params or {}).get("container")
        found = await asyncio.to_thread(docker.discover_docker)
        if found["error"]:
            raise rpc.RpcError(rpc.CONFLICT, found["error"])
        try:
            return dockerops, dockerops.find(found["containers"], name), found["containers"]
        except dockerops.DockerError as exc:
            raise rpc.RpcError(rpc.INVALID_PARAMS, str(exc)) from exc

    async def _docker_plan(self, params):
        mod, container, everything = await self._docker_target(params)
        kind = params.get("action")
        if kind not in mod.KINDS:
            raise rpc.RpcError(rpc.INVALID_PARAMS, f"action must be one of {', '.join(mod.KINDS)}")
        try:
            return await asyncio.to_thread(mod.plan_action, kind, container, everything, params.get("database") or None,
                                           params.get("update"), params.get("install"), None, params.get("demo", True) is not False)
        except mod.DockerError as exc:
            raise rpc.RpcError(rpc.INVALID_PARAMS, str(exc)) from exc

    async def h_docker_plan(self, params, _conn):
        """Dry run of start, stop, restart or a module upgrade in a container: checks, steps. Changes nothing."""
        return (await self._docker_plan(params)).as_dict()

    async def h_docker_run(self, params, _conn):
        """Run a container action in the background. Progress arrives as docker.step, the end as docker.finished."""
        from . import dockerops

        plan = await self._docker_plan(params)
        if not plan.ok:
            raise rpc.RpcError(rpc.CONFLICT, "; ".join(c.detail for c in plan.checks if c.status == "fail"))

        async def work(report) -> dict:
            return await dockerops.run_action(plan, report)

        run_id = self._start_job("docker", work, {"container": plan.container, "action": plan.kind})
        return {"run_id": run_id, "container": plan.container}

    async def h_docker_logs(self, params, _conn):
        """The last lines of a container's log, grouped like a session log (levels, tracebacks, repeats)."""
        from . import logs

        mod, container, _all = await self._docker_target(params)
        tail = (params or {}).get("tail", 500)
        if not isinstance(tail, int) or isinstance(tail, bool):
            raise rpc.RpcError(rpc.INVALID_PARAMS, "tail must be a number")
        try:
            text = await asyncio.to_thread(mod.read_logs, container["name"], tail)
        except mod.DockerError as exc:
            raise rpc.RpcError(rpc.CONFLICT, str(exc)) from exc
        return {"text": text, "analysis": logs.analyze(text)}

    async def h_docker_shell(self, params, _conn):
        """The command that opens ``odoo shell`` in the container. It needs a terminal: the app shows it, you run it."""
        import shlex

        mod, container, _all = await self._docker_target(params)
        try:
            return {"command": shlex.join(mod.shell_argv(container, (params or {}).get("database") or ""))}
        except mod.DockerError as exc:
            raise rpc.RpcError(rpc.INVALID_PARAMS, str(exc)) from exc

    async def h_compare_installations(self, params, _conn):
        """Difference between two installations as a whole: version, commit, Python, venv packages."""
        from . import compare
        from .discover import scan

        roots = [(params or {}).get(k) for k in ("a", "b")]
        if not all(isinstance(r, str) and r for r in roots):
            raise rpc.RpcError(rpc.INVALID_PARAMS, "a and b are installation roots")
        snap = await asyncio.to_thread(scan.scan, None, False)
        try:
            return await asyncio.to_thread(compare.compare_installations, roots[0], roots[1], snap)
        except KeyError as exc:
            raise rpc.RpcError(rpc.INVALID_PARAMS, f"{exc.args[0]} is not a discovered installation") from exc

    async def h_compare_databases(self, params, _conn):
        """Modules that are installed in one database and not the other, or differ in state or version."""
        from . import compare, dbquery

        sides = []
        for key in ("a", "b"):
            side = (params or {}).get(key) or {}
            if not isinstance(side.get("root"), str) or not isinstance(side.get("database"), str) or not side["database"]:
                raise rpc.RpcError(rpc.INVALID_PARAMS, f"{key} needs root and database")
            sides.append(side)
        try:
            a, b = [await dbquery.installed_modules(s["root"], s["database"]) for s in sides]
        except dbquery.QueryError as exc:
            raise rpc.RpcError(rpc.CONFLICT, str(exc)) from exc
        return {"a": sides[0], "b": sides[1], "modules": compare.diff_modules(a, b),
                "counts": {"a": len(a), "b": len(b)}}

    async def h_compare(self, params, _conn):
        """Difference between two discovered configs (``path`` and ``other``): facts, packages, addons_path, options."""
        from . import compare

        path, snap = await self._config(params)
        other = (params or {}).get("other")
        if not isinstance(other, str) or not any(i["path"] == other for i in snap["instances"]):
            raise rpc.RpcError(rpc.INVALID_PARAMS, f"{other} is not a discovered Odoo config")
        return await asyncio.to_thread(compare.compare, path, other, snap)

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
            if root.startswith("docker:"):
                from . import dockerdb, dockerops

                try:
                    return await dockerdb.prepare(root[len("docker:"):])
                except dockerops.DockerError as exc:
                    raise context.NotFound(str(exc)) from exc
            return await context.prepare(root)
        except context.NotFound as exc:
            raise rpc.RpcError(rpc.INVALID_PARAMS, str(exc)) from exc

    @staticmethod
    def _db_module(params):
        """``database.ops`` for an installation root, ``dockerdb`` for ``docker:<container>``: same interface."""
        from . import dockerdb
        from .database import ops

        return dockerdb if str((params or {}).get("root", "")).startswith("docker:") else ops

    async def h_db_list(self, params, _conn):
        """Databases of one installation's role, plus the recipes that can be run on them."""
        from .database import recipes

        _inst, ctx = await self._db_prepare(params)
        if self._db_module(params).__name__.endswith("dockerdb"):
            return {"databases": ctx.databases, "error": ctx.list_error, "recipes": await asyncio.to_thread(recipes.available),
                    "agent_running": None}
        return {"databases": ctx.databases, "error": getattr(ctx, "listing_error", None),
                "recipes": await asyncio.to_thread(recipes.available), "agent_running": ctx.agent_running}

    async def _db_plan(self, params):
        ops = self._db_module(params)
        inst, ctx = await self._db_prepare(params)
        fields = {k: params.get(k) or None for k in ("source", "target", "backup", "dest", "recipe", "confirm")}
        for key, value in fields.items():
            if value is not None and not isinstance(value, str):
                raise rpc.RpcError(rpc.INVALID_PARAMS, f"{key} must be a string")
        kind = params.get("action")
        if kind not in ops.KINDS:
            raise rpc.RpcError(rpc.INVALID_PARAMS, f"action must be one of {', '.join(ops.KINDS)}")
        keep = params.get("keep")
        if keep is not None and (not isinstance(keep, int) or isinstance(keep, bool)):
            raise rpc.RpcError(rpc.INVALID_PARAMS, "keep must be a number")
        return await asyncio.to_thread(ops.plan_db, kind, inst, ctx, keep=keep, **fields)

    async def h_db_plan(self, params, _conn):
        """Dry run of a database action: checks, steps. Changes nothing. The password never leaves the sidecar."""
        return (await self._db_plan(params)).as_dict()

    async def h_db_run(self, params, _conn):
        """Run a database action in the background. Progress arrives as db.step, the end as db.finished."""
        ops = self._db_module(params)
        plan = await self._db_plan(params)
        if not plan.ok:
            raise rpc.RpcError(rpc.CONFLICT, "; ".join(c.detail for c in plan.checks if c.status == "fail"))

        async def work(report) -> dict:
            return await ops.run_db(plan, report)

        run_id = self._start_job("db", work, {"root": plan.root, "action": plan.kind, "source": plan.source, "target": plan.target})
        return {"run_id": run_id, "root": plan.root}

    async def h_db_snapshots(self, params, _conn):
        """Snapshots of one installation or container (optionally of one database), newest first."""
        from . import dockerdb
        from .database import ops

        inst, ctx = await self._db_prepare(params)
        database = (params or {}).get("database") or None
        if self._db_module(params) is dockerdb:
            return {"snapshots": await asyncio.to_thread(dockerdb.list_snapshots, inst["name"], database, ctx.home), "error": None}
        try:
            return {"snapshots": await ops.list_snapshots(inst, ctx, database), "error": None}
        except ops.DbError as exc:
            return {"snapshots": [], "error": str(exc)}

    async def h_discover(self, params, _conn):
        from pathlib import Path

        from .discover import scan

        roots = [Path(r) for r in (params or {}).get("roots") or []] or None
        return await asyncio.to_thread(scan.scan, roots, not (params or {}).get("no_databases"))

    async def h_services_list(self, params, _conn):
        from . import services

        return await asyncio.to_thread(services.list_services)

    async def h_services_action(self, params, _conn):
        from . import services

        p = params or {}
        self._unlocking, self._purpose = pwd.getpwuid(os.getuid()).pw_name, "service"
        try:
            output = await services.run_action_askpass(p.get("action", ""), p.get("name", ""), {"ODP_ASKPASS_SOCK": self.askpass_path})
        except services.ServiceError as exc:
            raise rpc.RpcError(rpc.INVALID_PARAMS, str(exc)) from exc
        finally:
            self._unlocking, self._purpose = None, "unlock"
        return {"output": output}

    async def h_services_journal(self, params, _conn):
        from . import services

        p = params or {}
        try:
            return {"text": await services.journal_async(p.get("name", ""), int(p.get("lines") or 200), p.get("since"))}
        except services.ServiceError as exc:
            raise rpc.RpcError(rpc.INVALID_PARAMS, str(exc)) from exc

    async def h_services_show(self, params, _conn):
        from . import services

        try:
            return await asyncio.to_thread(services.read_unit, (params or {}).get("name", ""))
        except services.ServiceError as exc:
            raise rpc.RpcError(rpc.INVALID_PARAMS, str(exc)) from exc

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
