"""Z1/Z2: read-only Odoo metadata (models, fields, relations, external IDs) and PostgreSQL sizes and bounded counts.

Odoo metadata comes from the ir_* tables; PostgreSQL facts from pg_class. They are returned apart and never mixed
(a model's table name is derived, and said so). Every query is read-only, bounded (LIMIT, statement_timeout) and
built from fixed text plus validated names: search text is restricted to letters, digits, ``_ . -`` and spaces.
"""

from __future__ import annotations

import json
import re

from .. import dbquery

TIMEOUT_MS = 10_000
COUNT_CAP = 100_000
PAGE_MAX = 200
_SEARCH = re.compile(r"^[A-Za-z0-9_. -]{0,60}$")
_MODEL = re.compile(r"^[a-z0-9_][a-z0-9_.]{0,127}$")
_TABLE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class MetaError(ValueError):
    pass


def _search(q) -> str:
    if q in (None, ""):
        return ""
    if not isinstance(q, str) or not _SEARCH.match(q):
        raise MetaError("search text: letters, digits, _ . - and spaces (up to 60)")
    return q.strip()


def _page(limit, offset) -> tuple[int, int]:
    try:
        limit, offset = int(limit if limit is not None else 50), int(offset or 0)
    except (TypeError, ValueError) as exc:
        raise MetaError("limit and offset are numbers") from exc
    if not 1 <= limit <= PAGE_MAX or offset < 0:
        raise MetaError(f"limit 1-{PAGE_MAX}, offset 0 or more")
    return limit, offset


def _model(name) -> str:
    if not isinstance(name, str) or not _MODEL.match(name):
        raise MetaError(f"bad model name {name!r}")
    return name


def label(value) -> str | None:
    """ir_model.name / field_description: text before Odoo 16, a JSON translation map after."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get("en_US") or next(iter(value.values()), None)
    if isinstance(value, str) and value.startswith("{"):
        try:
            data = json.loads(value)
            if isinstance(data, dict):
                return data.get("en_US") or next(iter(data.values()), None)
        except ValueError:
            pass
    return value


def table_of(model: str) -> str:
    return model.replace(".", "_")


async def _q(root: str, database: str, sql: str) -> list[dict]:
    try:
        return await dbquery.json_rows(root, database, sql, TIMEOUT_MS)
    except dbquery.QueryError as exc:
        raise MetaError(str(exc)) from exc


async def models(root: str, database: str, q=None, limit=50, offset=0) -> dict:
    q = _search(q)
    limit, offset = _page(limit, offset)
    where = f"WHERE m.model ILIKE '%{q}%' OR m.name::text ILIKE '%{q}%'" if q else ""
    sql = (
        "SELECT m.model, m.name::text AS name, m.transient, m.state, "
        "(SELECT string_agg(DISTINCT d.module, ',' ORDER BY d.module) FROM ir_model_data d "
        " WHERE d.model = 'ir.model' AND d.res_id = m.id) AS modules, "
        "(SELECT count(*) FROM ir_model_fields f WHERE f.model_id = m.id) AS fields, "
        "count(*) OVER () AS total "
        f"FROM ir_model m {where} ORDER BY m.model LIMIT {limit} OFFSET {offset}")
    rows = await _q(root, database, sql)
    total = rows[0]["total"] if rows else 0
    return {"source": "odoo", "total": total, "limit": limit, "offset": offset,
            "models": [{**{k: r[k] for k in ("model", "transient", "state", "fields")}, "name": label(r["name"]),
                        "modules": (r["modules"] or "").split(",") if r["modules"] else [], "table": table_of(r["model"])}
                       for r in rows]}


async def model(root: str, database: str, name: str) -> dict:
    """One model: its fields (type, relation, stored, related, required, custom) and the fields that point to it.
    ir_model_fields does not say whether a Python field is computed; store = false is the closest fact."""
    name = _model(name)
    fields_sql = (
        "SELECT f.name, f.ttype, f.relation, f.relation_field, f.relation_table, f.required, f.readonly, f.store, "
        "f.state, f.related, f.field_description::text AS label, "
        "(SELECT string_agg(DISTINCT d.module, ',' ORDER BY d.module) FROM ir_model_data d "
        " WHERE d.model = 'ir.model.fields' AND d.res_id = f.id) AS modules "
        f"FROM ir_model_fields f WHERE f.model = '{name}' ORDER BY f.name")
    incoming_sql = (
        "SELECT f.model, f.name, f.ttype FROM ir_model_fields f "
        f"WHERE f.relation = '{name}' AND f.ttype IN ('many2one', 'many2many', 'one2many') ORDER BY f.model, f.name LIMIT 500")
    fields = await _q(root, database, fields_sql)
    if not fields:
        raise MetaError(f"no model {name} in {database}")
    incoming = await _q(root, database, incoming_sql)
    return {"source": "odoo", "model": name, "table": table_of(name),
            "fields": [{**f, "label": label(f["label"]), "modules": (f["modules"] or "").split(",") if f["modules"] else []}
                       for f in fields],
            "incoming": incoming,
            "outgoing": [{"field": f["name"], "ttype": f["ttype"], "model": f["relation"]} for f in fields
                         if f["relation"] and f["ttype"] in ("many2one", "many2many", "one2many")]}


async def xmlids(root: str, database: str, q=None, model_name=None, limit=50, offset=0) -> dict:
    q = _search(q)
    limit, offset = _page(limit, offset)
    cond = []
    if q:
        cond.append(f"(d.module || '.' || d.name) ILIKE '%{q}%'")
    if model_name:
        cond.append(f"d.model = '{_model(model_name)}'")
    where = ("WHERE " + " AND ".join(cond)) if cond else ""
    sql = ("SELECT d.module, d.name, d.model, d.res_id, d.noupdate, count(*) OVER () AS total FROM ir_model_data d "
           f"{where} ORDER BY d.module, d.name LIMIT {limit} OFFSET {offset}")
    rows = await _q(root, database, sql)
    return {"source": "odoo", "total": rows[0]["total"] if rows else 0, "limit": limit, "offset": offset,
            "xmlids": [{k: r[k] for k in ("module", "name", "model", "res_id", "noupdate")} for r in rows]}


async def sizes(root: str, database: str, limit=50) -> dict:
    """PostgreSQL: database size and the largest tables (with row estimates from pg_class.reltuples)."""
    limit, _ = _page(limit, 0)
    sql = ("SELECT c.relname AS table, pg_total_relation_size(c.oid) AS total_bytes, pg_relation_size(c.oid) AS table_bytes, "
           "c.reltuples::bigint AS estimate, pg_database_size(current_database()) AS database_bytes, "
           "count(*) OVER () AS tables "
           "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
           f"WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY 2 DESC LIMIT {limit}")
    rows = await _q(root, database, sql)
    return {"source": "postgresql", "database_bytes": rows[0]["database_bytes"] if rows else None,
            "tables_total": rows[0]["tables"] if rows else 0,
            "tables": [{k: r[k] for k in ("table", "total_bytes", "table_bytes", "estimate")} for r in rows],
            "note": "estimates come from the last ANALYZE; counts are exact only when asked"}


async def count(root: str, database: str, table: str, cap: int = COUNT_CAP) -> dict:
    """Exact row count of one table up to ``cap`` (more shows as cap+, never a full scan of a huge table)."""
    if not isinstance(table, str) or not _TABLE.match(table):
        raise MetaError(f"bad table name {table!r}")
    exists = await _q(root, database, "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                                      f"WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname = '{table}'")
    if not exists:
        raise MetaError(f"no table {table} in {database}")
    rows = await _q(root, database, f'SELECT count(*) AS n FROM (SELECT 1 FROM "{table}" LIMIT {int(cap) + 1}) s')
    n = rows[0]["n"]
    return {"source": "postgresql", "table": table, "count": min(n, cap), "capped": n > cap, "cap": cap}
