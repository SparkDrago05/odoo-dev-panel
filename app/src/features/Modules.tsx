import { ArrowLeft, Boxes, FolderPlus, Search, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { rpc } from "../rpc";
import { Dialog } from "../ui/Dialog";
import { Badge, Callout, Dot, Field, Loading } from "../ui/primitives";

type Module = {
  db_state?: string | null; db_version?: string | null; version_differs?: boolean;
  path: string; addons_path: string; depends: string[]; required_by: string[]; installable: boolean;
  name?: string; version?: string; application?: boolean; auto_install?: boolean | string[];
};
type Focus = { name: string; module: Module; needs: Record<string, number>; needed_by: Record<string, number>; missing: Record<string, string[]> };
type Graph = {
  modules: Record<string, Module>; missing: Record<string, string[]>; shadowed: { name: string; path: string; by: string }[];
  unreadable: string[]; cycles: string[][]; addons_paths: string[]; focus?: Focus;
  installation: string | null; series: string | null; extra: string[];
  db_error: string | null; db_only?: string[]; db_counts?: Record<string, number>;
};

const stateTone = (s: string | null | undefined) => (s === "installed" ? "ok" : s === undefined || s === null ? "idle" : "warn");
const stateLabel = (m: Module) => (m.db_state === undefined ? "" : m.db_state ?? "not in database");

const COL = 196;
const ROW = 30;
const BOX = 156;

/** Layered picture of one module: what it needs on the left (farthest first), the module in the middle, what needs it
 * on the right. An arrow runs from a module to the one that depends on it. */
function Picture({ graph, focus, onPick }: { graph: Graph; focus: Focus; onPick: (name: string) => void }) {
  const left = new Map<number, string[]>();
  const right = new Map<number, string[]>();
  for (const [n, d] of Object.entries(focus.needs)) left.set(d, [...(left.get(d) ?? []), n]);
  for (const [n, d] of Object.entries(focus.needed_by)) right.set(d, [...(right.get(d) ?? []), n]);
  const lmax = Math.max(0, ...left.keys());
  const rmax = Math.max(0, ...right.keys());
  const total = Object.keys(focus.needs).length + Object.keys(focus.needed_by).length + 1;
  if (total > 80) return <Callout tone="info">{total} modules: too many to draw. Limit the depth to 1 or 2.</Callout>;
  const pos = new Map<string, { x: number; y: number }>();
  const place = (names: string[], col: number) => names.sort().forEach((n, i) => pos.set(n, { x: col * COL + 12, y: i * ROW + 12 }));
  for (let d = 1; d <= lmax; d++) place(left.get(d) ?? [], lmax - d);
  place([focus.name], lmax);
  for (let d = 1; d <= rmax; d++) place(right.get(d) ?? [], lmax + d);
  const rows = Math.max(1, ...[...left.values(), ...right.values()].map((v) => v.length));
  const width = (lmax + rmax + 1) * COL + 12;
  const height = rows * ROW + 20;
  const edges: [string, string][] = [];
  for (const n of pos.keys()) for (const dep of graph.modules[n]?.depends ?? []) if (pos.has(dep)) edges.push([dep, n]);
  const color = (n: string) => {
    const s = graph.modules[n]?.db_state;
    return s === "installed" ? "var(--success)" : s === undefined || s === null ? "var(--text-3)" : "var(--warning)";
  };
  return (
    <div className="graph">
      <svg width={width} height={height} role="img" aria-label={`Dependencies of ${focus.name}`} style={{ display: "block", color: "var(--text-2)" }}>
        <defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="currentColor" opacity="0.6" /></marker></defs>
        {edges.map(([a, b]) => {
          const p = pos.get(a)!, q = pos.get(b)!;
          const x1 = p.x + BOX, y1 = p.y + 12, x2 = q.x - 2, y2 = q.y + 12, mx = (x1 + x2) / 2;
          return <path key={`${a}>${b}`} d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`} fill="none" stroke="currentColor" opacity="0.35" markerEnd="url(#arrow)" />;
        })}
        {[...pos.entries()].map(([n, p]) => {
          const isFocus = n === focus.name;
          return (
            <g key={n} transform={`translate(${p.x},${p.y})`} style={{ cursor: "pointer" }} onClick={() => onPick(n)}>
              <rect width={BOX} height="24" rx="6" fill={isFocus ? "var(--accent-bg)" : "var(--surface)"} stroke={isFocus ? "var(--accent)" : "var(--border-strong)"} />
              <circle cx="11" cy="12" r="3.5" fill={color(n)} />
              <text x="21" y="16" fontSize="11.5" fill="var(--text)">{n.length > 21 ? n.slice(0, 20) + "…" : n}</text>
              <title>{n}{graph.modules[n]?.db_state !== undefined ? ` · ${stateLabel(graph.modules[n])}` : ""}</title>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

const byDistance = (m: Record<string, number>) => Object.entries(m).sort((a, b) => a[1] - b[1] || a[0].localeCompare(b[0]));

/** Module analyzer for one config: manifests on the addons_path (read, never imported), optional state from a database. */
export function ModulesPanel({ path }: { path: string }) {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [module, setModule] = useState<string | null>(null);
  const [depth, setDepth] = useState<number | null>(null);
  const [database, setDatabase] = useState("");
  const [dbs, setDbs] = useState<string[]>([]);
  const [dbNote, setDbNote] = useState<string | null>(null);
  const [extra, setExtra] = useState<string[]>([]);
  const [extraInput, setExtraInput] = useState("");
  const [root, setRoot] = useState<string | null>(null);

  useEffect(() => { setModule(null); setDatabase(""); setExtra([]); setGraph(null); }, [path]);
  useEffect(() => {
    setError(null);
    rpc.request<Graph>("modules.graph", { path, module, depth, database: database || undefined, extra })
      .then((g) => { setGraph(g); setRoot(g.installation); }).catch((e) => setError(String(e.message)));
  }, [path, module, depth, database, extra]);

  // The databases of the installation, to read module states from. Loaded once the installation is known.
  useEffect(() => {
    if (!root) return;
    rpc.request<{ databases: { name: string }[]; error: string | null }>("db.list", { root })
      .then((l) => { setDbs(l.databases.map((d) => d.name)); setDbNote(l.error); })
      .catch((e) => setDbNote(String(e.message)));
  }, [root]);

  const f = graph?.focus;
  const names = useMemo(() => (graph ? Object.keys(graph.modules).sort().filter((n) => n.includes(filter.trim().toLowerCase())) : []), [graph, filter]);
  const link = (name: string) => <button key={name} className="chip" onClick={() => setModule(name)}>{name}</button>;
  const differs = graph ? Object.values(graph.modules).filter((m) => m.version_differs).length : 0;

  if (error) return <Callout tone="bad">{error}</Callout>;
  if (!graph) return <Loading>Reading manifests…</Loading>;
  return (
    <div className="stack">
      <div className="row">
        <Badge>{Object.keys(graph.modules).length} modules</Badge>
        <Badge>{graph.addons_paths.length} folders</Badge>
        {graph.series && <Badge mono>{graph.series}</Badge>}
        <span className="xs dim">Manifests are read, never imported.</span>
        <span className="grow" />
        <label className="row tight small muted">State in database
          <select value={database} onChange={(e) => setDatabase(e.target.value)} disabled={dbs.length === 0} aria-label="Database for module states">
            <option value="">none</option>
            {dbs.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </label>
      </div>
      {dbNote && dbs.length === 0 && <span className="xs dim">{dbNote}</span>}
      {graph.cycles.map((c, i) => <Callout key={i} tone="bad" title="Dependency cycle">{c.join(" → ")}</Callout>)}
      {Object.keys(graph.missing).length > 0 && (
        <Callout tone="warn" title="Missing dependencies">{Object.entries(graph.missing).map(([m, d]) => `${m} needs ${d.join(", ")}`).join("; ")}</Callout>
      )}
      {graph.shadowed.length > 0 && <Callout tone="info" title="Hidden by an earlier folder"><span className="mono small">{graph.shadowed.map((s) => s.path).join(", ")}</span></Callout>}
      {graph.unreadable.length > 0 && <Callout tone="warn" title="Manifest not readable"><span className="mono small">{graph.unreadable.join(", ")}</span></Callout>}
      {graph.db_error && <Callout tone="bad">{graph.db_error}</Callout>}
      {graph.db_counts && (
        <div className="row">
          {Object.entries(graph.db_counts).map(([k, v]) => <Badge key={k} tone={k === "installed" ? "ok" : "warn"}>{v} {k}</Badge>)}
          {differs > 0 && <span className="small muted">{differs} installed at another version than the manifest (after a version upgrade: run -u on them)</span>}
        </div>
      )}
      {graph.db_only && graph.db_only.length > 0 && (
        <Callout tone="bad" title={`In the database but not in the addons_path (${graph.db_only.length})`}>
          <span className="mono small">{graph.db_only.join(", ")}</span>. Odoo cannot load them; add their folder below or uninstall them.
        </Callout>
      )}
      <div className="row nowrap">
        <input className="mono grow" value={extraInput} placeholder="Also look in a folder (absolute path)" aria-label="Extra addons folder" onChange={(e) => setExtraInput(e.target.value)} />
        <button className="btn" disabled={!extraInput.startsWith("/") || extra.includes(extraInput.trim())}
          onClick={() => { setExtra([...extra, extraInput.trim()]); setExtraInput(""); }}><FolderPlus />Add folder</button>
      </div>
      {extra.length > 0 && (
        <div className="row tight">
          {extra.map((x) => <button key={x} className="chip" aria-pressed onClick={() => setExtra(extra.filter((e) => e !== x))} title="Stop looking here"><span className="mono">{x}</span><X style={{ width: 12, height: 12 }} /></button>)}
        </div>
      )}
      {f ? (
        <div className="stack">
          <div className="row between">
            <button className="btn ghost sm" onClick={() => setModule(null)}><ArrowLeft />All modules</button>
            <Field label="Depth">
              <select value={depth ?? ""} onChange={(e) => setDepth(e.target.value ? Number(e.target.value) : null)}>
                <option value="">all</option>
                {[1, 2, 3, 4].map((d) => <option key={d} value={d}>{d}</option>)}
              </select>
            </Field>
          </div>
          <div className="row">
            <h3 className="mono">{f.name}</h3>
            {f.module.name && <span className="muted">{f.module.name}</span>}
            <Badge mono>{f.module.version ?? "no version"}</Badge>
            {!f.module.installable && <Badge tone="warn">not installable</Badge>}
            {f.module.db_state !== undefined && <Badge tone={stateTone(f.module.db_state) as "ok"}>{stateLabel(f.module)}{f.module.db_version ? ` ${f.module.db_version}` : ""}</Badge>}
          </div>
          <span className="mono xs dim break">{f.module.path}</span>
          <Picture graph={graph} focus={f} onPick={setModule} />
          <div className="grid2">
            <div className="stack tight">
              <span className="section-title">Needs ({Object.keys(f.needs).length})</span>
              <div className="row tight">{byDistance(f.needs).map(([n, d]) => <span key={n} className="row tight nowrap">{link(n)}{d > 1 && <span className="xs dim">{d}</span>}</span>)}
                {Object.keys(f.needs).length === 0 && <span className="muted small">nothing</span>}</div>
            </div>
            <div className="stack tight">
              <span className="section-title">Needed by ({Object.keys(f.needed_by).length}) · breaks if removed</span>
              <div className="row tight">{byDistance(f.needed_by).map(([n, d]) => <span key={n} className="row tight nowrap">{link(n)}{d > 1 && <span className="xs dim">{d}</span>}</span>)}
                {Object.keys(f.needed_by).length === 0 && <span className="muted small">nothing</span>}</div>
            </div>
          </div>
          {Object.entries(f.missing).map(([m, d]) => <Callout key={m} tone="bad">{m} needs {d.join(", ")}: not found in the addons_path</Callout>)}
        </div>
      ) : (
        <div className="panel">
          <div className="panel-head">
            <Search style={{ width: 14, height: 14, color: "var(--text-3)" }} />
            <input type="search" className="grow" style={{ border: 0, background: "transparent", boxShadow: "none" }} value={filter} placeholder="Filter modules" aria-label="Filter modules" onChange={(e) => setFilter(e.target.value)} />
            <span className="xs dim">{names.length} shown</span>
          </div>
          <div style={{ maxHeight: 460, overflow: "auto" }}>
            <table>
              <thead><tr><th>Module</th><th>Version</th>{database && <th>In {database}</th>}<th className="num">Depends</th><th className="num">Required by</th></tr></thead>
              <tbody>
                {names.map((n) => {
                  const m = graph.modules[n];
                  return (
                    <tr key={n}>
                      <td><a className="mono small" onClick={() => setModule(n)}>{n}</a>{!m.installable && <span className="xs dim"> · not installable</span>}</td>
                      <td className="mono small muted">{m.version ?? "–"}</td>
                      {database && (
                        <td className="small"><span className="row tight nowrap"><Dot tone={stateTone(m.db_state) as "ok"} />{stateLabel(m)}{m.db_version ? ` ${m.db_version}` : ""}{m.version_differs ? <Badge tone="warn">≠ manifest</Badge> : null}</span></td>
                      )}
                      <td className="num">{m.depends.length}</td>
                      <td className="num">{m.required_by.length}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

export function ModulesDialog({ path, onClose }: { path: string; onClose: () => void }) {
  return (
    <Dialog size="xl" icon={<Boxes />} title="Modules" subtitle={path} onClose={onClose}>
      <ModulesPanel path={path} />
    </Dialog>
  );
}
