"""One entry point per module-center operation, shared by ``odp modules <verb>`` and the sidecar."""

from __future__ import annotations

import asyncio

from .. import configedit, modules
from . import actions, center

ApiError = actions.ActionError


async def load(params: dict, snapshot: dict, with_changes: bool = True) -> dict:
    """Inventory of a config, with the installed state from ``database`` when given."""
    config, database = params.get("config"), params.get("database") or None
    if database is not None and not isinstance(database, str):
        raise ApiError("database is text")
    try:
        graph = await asyncio.to_thread(center.inventory, config, snapshot, with_changes)
    except (center.CenterError, configedit.ConfigError, OSError) as exc:
        raise ApiError(str(exc)) from exc
    graph["db_error"] = None
    graph["database"] = database
    if database:
        from .. import dbquery

        try:
            states = await dbquery.installed_modules(graph["installation"] or "", database)
            modules.overlay(graph, states, graph.get("series"))
        except dbquery.QueryError as exc:
            graph["db_error"] = str(exc)
    return graph


async def plan(kind: str, params: dict, snapshot: dict) -> dict:
    if kind not in ("upgrade", "install", "test"):
        raise ApiError("kind is upgrade, install or test")
    config = params.get("config")
    if kind == "test":
        graph = await load({"config": config}, snapshot, with_changes=False)
        demo = params.get("demo", True) is not False
        return await asyncio.to_thread(actions.test_plan, config, snapshot, graph, params.get("modules"),
                                       params.get("tags") or None, demo)
    graph = await load({"config": config, "database": params.get("database")}, snapshot, with_changes=False)
    return await asyncio.to_thread(actions.upgrade_plan, config, snapshot, graph, params.get("database"),
                                   params.get("modules"), kind == "install", params.get("snapshot", True) is not False)


async def execute(planned: dict, conn, report) -> dict:
    if planned["kind"] == "test":
        return await actions.test_run(planned, conn, report)
    return await actions.upgrade_run(planned, conn, report)
