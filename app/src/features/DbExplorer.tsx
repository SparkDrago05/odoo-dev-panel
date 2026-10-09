import { ArrowLeft, Database, Hash, KeyRound, Search, Table2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { rpc } from "../rpc";
import { Dialog } from "../ui/Dialog";
import { Badge, Callout, Loading } from "../ui/primitives";
import { Tabs } from "../ui/Tabs";

type ModelRow = { model: string; name: string | null; transient: boolean; state: string; modules: string[]; fields: number; table: string };
type FieldRow = { name: string; ttype: string; relation: string | null; relation_field: string | null; relation_table: string | null;
  required: boolean; readonly: boolean; store: boolean; state: string; related: string | null; label: string | null; modules: string[] };
type ModelDetail = { model: string; table: string; fields: FieldRow[]; incoming: { model: string; name: string; ttype: string }[];
  outgoing: { field: string; ttype: string; model: string }[] };
type XmlId = { module: string; name: string; model: string; res_id: number; noupdate: boolean };
type Sizes = { database_bytes: number | null; tables_total: number; tables: { table: string; total_bytes: number; table_bytes: number; estimate: number }[]; note: string };

const PAGE = 50;
const SEARCH = /^[A-Za-z0-9_. -]{0,60}$/;
const mb = (b: number) => (b >= 1e9 ? `${(b / 1e9).toFixed(2)} GB` : `${(b / 1e6).toFixed(1)} MB`);

function useQuery<T>(method: string, params: Record<string, unknown> | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = JSON.stringify(params);
  useEffect(() => {
    if (!params) return;
    let live = true;
    setData(null); setError(null);
    rpc.request<T>(method, params).then((d) => live && setData(d)).catch((e) => live && setError(String(e.message)));
    return () => { live = false; };
  }, [method, key]); // eslint-disable-line react-hooks/exhaustive-deps
  return { data, error };
}

function SearchBox({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  const bad = !SEARCH.test(value);
  return (
    <div className="row tight" style={{ flex: 1 }}>
      <Search style={{ width: 14, color: "var(--text-3)" }} />
      <input className="mono grow" value={value} placeholder={placeholder} aria-label="Search" aria-invalid={bad} onChange={(e) => onChange(e.target.value)} />
      {bad && <span className="xs bad-text">letters, digits, _ . - and spaces</span>}
    </div>
  );
}

function Pager({ total, offset, setOffset }: { total: number; offset: number; setOffset: (n: number) => void }) {
  if (total <= PAGE) return <span className="xs dim">{total} total</span>;
  return (
    <div className="row tight">
      <button className="btn ghost sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
      <span className="xs dim">{offset + 1}–{Math.min(total, offset + PAGE)} of {total}</span>
      <button className="btn ghost sm" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Next</button>
    </div>
  );
}

function useDebounced(value: string, ms = 300) {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}

function ModelsTab({ base, open }: { base: { root: string; database: string }; open: (m: string) => void }) {
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const dq = useDebounced(q);
  useEffect(() => setOffset(0), [dq]);
  const { data, error } = useQuery<{ total: number; models: ModelRow[] }>("db.models", SEARCH.test(dq) ? { ...base, q: dq, limit: PAGE, offset } : null);
  return (
    <div className="stack tight">
      <div className="row"><SearchBox value={q} onChange={setQ} placeholder="res.partner, sale, Contact…" />{data && <Pager total={data.total} offset={offset} setOffset={setOffset} />}</div>
      {error && <Callout tone="bad">{error}</Callout>}
      {!data && !error ? <Loading /> : data && (
        <div className="list explorer-list">
          {data.models.map((m) => (
            <div key={m.model} className="list-row clickable" role="button" tabIndex={0} onClick={() => open(m.model)} onKeyDown={(e) => { if (e.key === "Enter") open(m.model); }}>
              <span className="mono small strong" style={{ minWidth: 220 }}>{m.model}</span>
              <span className="small truncate grow">{m.name}</span>
              {m.transient && <Badge>transient</Badge>}
              {m.state === "manual" && <Badge tone="info">custom</Badge>}
              <span className="xs dim nowrap">{m.fields} fields</span>
              <span className="xs dim truncate" style={{ maxWidth: 220 }} title={m.modules.join(", ")}>{m.modules.slice(0, 3).join(", ")}{m.modules.length > 3 ? ` +${m.modules.length - 3}` : ""}</span>
            </div>
          ))}
          {data.models.length === 0 && <p className="muted" style={{ padding: 12 }}>No model matches.</p>}
        </div>
      )}
    </div>
  );
}

function ModelView({ base, name, open, back, count }: { base: { root: string; database: string }; name: string; open: (m: string) => void; back: () => void; count: (t: string) => void }) {
  const { data, error } = useQuery<ModelDetail>("db.model", { ...base, model: name });
  const [q, setQ] = useState("");
  const fields = (data?.fields ?? []).filter((f) => !q || f.name.includes(q) || (f.label ?? "").toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="stack tight">
      <div className="row">
        <button className="btn ghost sm" onClick={back}><ArrowLeft />Models</button>
        <span className="mono strong">{name}</span>
        {data && <span className="xs dim">table <code>{data.table}</code> (derived from the name)</span>}
        <span className="grow" />
        {data && <button className="btn sm" onClick={() => count(data.table)}><Hash />Count rows</button>}
      </div>
      {error && <Callout tone="bad">{error}</Callout>}
      {!data && !error ? <Loading /> : data && (
        <>
          <SearchBox value={q} onChange={setQ} placeholder="filter fields" />
          <div className="list explorer-list">
            {fields.map((f) => (
              <div key={f.name} className="list-row" style={{ alignItems: "flex-start" }}>
                <span className="mono small strong" style={{ minWidth: 220 }}>{f.name}</span>
                <span className="small grow truncate" title={f.label ?? ""}>{f.label}</span>
                <Badge mono>{f.ttype}</Badge>
                {f.relation && <button type="button" className="frame-link mono xs" onClick={() => open(f.relation!)}>{f.relation}</button>}
                {f.required && <Badge tone="warn">required</Badge>}
                {!f.store && <Badge>not stored</Badge>}
                {f.related && <Badge title={f.related}>related</Badge>}
                {f.state === "manual" && <Badge tone="info">custom</Badge>}
              </div>
            ))}
          </div>
          <span className="section-title">Pointed to by ({data.incoming.length})</span>
          <div className="row tight wrap">
            {data.incoming.slice(0, 200).map((f) => (
              <button key={`${f.model}.${f.name}`} type="button" className="chip mono" title={f.ttype} onClick={() => open(f.model)}>{f.model}.{f.name}</button>
            ))}
            {data.incoming.length === 0 && <span className="muted small">No relational field points to this model.</span>}
          </div>
        </>
      )}
    </div>
  );
}

function XmlIdsTab({ base, model }: { base: { root: string; database: string }; model?: string }) {
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const dq = useDebounced(q);
  useEffect(() => setOffset(0), [dq]);
  const { data, error } = useQuery<{ total: number; xmlids: XmlId[] }>("db.xmlids", SEARCH.test(dq) ? { ...base, q: dq, model, limit: PAGE, offset } : null);
  return (
    <div className="stack tight">
      <div className="row"><SearchBox value={q} onChange={setQ} placeholder="base.main_company, sale.…" />{data && <Pager total={data.total} offset={offset} setOffset={setOffset} />}</div>
      {error && <Callout tone="bad">{error}</Callout>}
      {!data && !error ? <Loading /> : data && (
        <div className="list explorer-list">
          {data.xmlids.map((x) => (
            <div key={`${x.module}.${x.name}`} className="list-row">
              <span className="mono small strong truncate grow">{x.module}.{x.name}</span>
              <span className="mono xs" style={{ minWidth: 200 }}>{x.model}</span>
              <span className="mono xs dim" style={{ minWidth: 60, textAlign: "right" }}>{x.res_id}</span>
              {x.noupdate && <Badge>noupdate</Badge>}
            </div>
          ))}
          {data.xmlids.length === 0 && <p className="muted" style={{ padding: 12 }}>No external ID matches.</p>}
        </div>
      )}
    </div>
  );
}

function SizeTab({ base, counts, count }: { base: { root: string; database: string }; counts: Record<string, string>; count: (t: string) => void }) {
  const { data, error } = useQuery<Sizes>("db.sizes", { ...base, limit: 100 });
  if (error) return <Callout tone="bad">{error}</Callout>;
  if (!data) return <Loading />;
  const max = data.tables[0]?.total_bytes || 1;
  return (
    <div className="stack tight">
      <span className="small">Database <strong>{data.database_bytes != null ? mb(data.database_bytes) : "?"}</strong>, {data.tables_total} tables. <span className="dim">PostgreSQL facts; rows are estimates from the last ANALYZE.</span></span>
      <div className="list explorer-list">
        {data.tables.map((t) => (
          <div key={t.table} className="list-row">
            <span className="mono small truncate" style={{ width: 280 }}>{t.table}</span>
            <div className="size-bar grow" aria-hidden><span style={{ width: `${(t.total_bytes / max) * 100}%` }} /></div>
            <span className="mono xs nowrap" style={{ minWidth: 80, textAlign: "right" }}>{mb(t.total_bytes)}</span>
            <span className="mono xs dim nowrap" style={{ minWidth: 100, textAlign: "right" }}>{counts[t.table] ?? `~${t.estimate.toLocaleString()}`}</span>
            <button className="btn ghost sm" title="Exact count, capped at 100000" onClick={() => count(t.table)}><Hash /></button>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Z1/Z2: Odoo metadata (models, fields, relations, external IDs) and PostgreSQL sizes of one database. Read-only. */
export function DbExplorerDialog({ root, database, onClose }: { root: string; database: string; onClose: () => void }) {
  const [tab, setTab] = useState<"models" | "xmlids" | "size">("models");
  const [model, setModel] = useState<string | null>(null);
  const [counts, setCounts] = useState<Record<string, string>>({});
  const [countError, setCountError] = useState<string | null>(null);
  const base = { root, database };
  const count = useCallback(async (table: string) => {
    setCounts((c) => ({ ...c, [table]: "counting…" }));
    try {
      const r = await rpc.request<{ count: number; capped: boolean }>("db.count", { root, database, table });
      setCounts((c) => ({ ...c, [table]: `${r.count.toLocaleString()}${r.capped ? "+" : ""} rows` }));
    } catch (e) { setCounts((c) => ({ ...c, [table]: "?" })); setCountError(String((e as Error).message)); }
  }, [root, database]);
  const openModel = (m: string) => { setModel(m); setTab("models"); };
  return (
    <Dialog size="xl" icon={<Database />} title={`Explore ${database}`} subtitle={`${root} · read-only`} onClose={onClose}
      footer={<button className="btn" onClick={onClose}>Close</button>}>
      <Tabs label="Explore" value={tab} onChange={(t) => { setTab(t); if (t !== "models") setModel(null); }}
        tabs={[{ id: "models", label: "Models", icon: <Table2 /> }, { id: "xmlids", label: "External IDs", icon: <KeyRound /> }, { id: "size", label: "Size", icon: <Hash /> }]} />
      {countError && <Callout tone="bad">{countError}</Callout>}
      {tab === "models" && (model
        ? <ModelView base={base} name={model} open={openModel} back={() => setModel(null)} count={(t) => { count(t); }} />
        : <ModelsTab base={base} open={openModel} />)}
      {tab === "models" && model && counts[model.replace(/\./g, "_")] && <span className="small">{model.replace(/\./g, "_")}: {counts[model.replace(/\./g, "_")]}</span>}
      {tab === "xmlids" && <XmlIdsTab base={base} />}
      {tab === "size" && <SizeTab base={base} counts={counts} count={count} />}
    </Dialog>
  );
}
