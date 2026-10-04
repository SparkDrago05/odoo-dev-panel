import { useEffect, useState } from "react";
import { rpc } from "./rpc";

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

const STATE_DOT: Record<string, string> = { installed: "running", "to upgrade": "stopping", "to install": "stopping", "to remove": "stopping" };
const stateLabel = (m: Module) => (m.db_state === undefined ? "" : m.db_state ?? "not in database");

const COL = 190;
const ROW = 28;

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
  if (total > 80) return <p className="muted">{total} modules: too many to draw. Limit the depth to 1 or 2.</p>;
  const pos = new Map<string, { x: number; y: number }>();
  const place = (names: string[], col: number) => names.sort().forEach((n, i) => pos.set(n, { x: col * COL + 8, y: i * ROW + 8 }));
  for (let d = 1; d <= lmax; d++) place(left.get(d) ?? [], lmax - d);
  place([focus.name], lmax);
  for (let d = 1; d <= rmax; d++) place(right.get(d) ?? [], lmax + d);
  const rows = Math.max(1, ...[...left.values(), ...right.values()].map((v) => v.length));
  const width = (lmax + rmax + 1) * COL;
  const height = rows * ROW + 16;
  const edges: [string, string][] = [];
  for (const n of pos.keys()) for (const dep of graph.modules[n]?.depends ?? []) if (pos.has(dep)) edges.push([dep, n]);
  const fill = (n: string) => {
    const s = graph.modules[n]?.db_state;
    return s === "installed" ? "var(--ok, #2e7d32)" : s === undefined ? "transparent" : s === null ? "transparent" : "var(--warn, #b26a00)";
  };
  return (
    <div style={{ overflow: "auto", maxHeight: 420 }}>
      <svg width={width} height={height} role="img" aria-label={`Dependencies of ${focus.name}`}>
        <defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="currentColor" opacity="0.5" /></marker></defs>
        {edges.map(([a, b]) => {
          const p = pos.get(a)!, q = pos.get(b)!;
          return <line key={`${a}>${b}`} x1={p.x + 150} y1={p.y + 11} x2={q.x} y2={q.y + 11} stroke="currentColor" opacity="0.25" markerEnd="url(#arrow)" />;
        })}
        {[...pos.entries()].map(([n, p]) => (
          <g key={n} transform={`translate(${p.x},${p.y})`} style={{ cursor: "pointer" }} onClick={() => onPick(n)}>
            <rect width="150" height="22" rx="4" fill={fill(n)} fillOpacity={n === focus.name ? 0.35 : 0.18} stroke="currentColor" strokeOpacity={n === focus.name ? 0.9 : 0.35} />
            <text x="6" y="15" fontSize="11" fill="currentColor">{n.length > 22 ? n.slice(0, 21) + "…" : n}</text>
            <title>{n}{graph.modules[n]?.db_state !== undefined ? ` · ${stateLabel(graph.modules[n])}` : ""}</title>
          </g>
        ))}
      </svg>
    </div>
  );
}

const byDistance = (m: Record<string, number>) => Object.entries(m).sort((a, b) => a[1] - b[1] || a[0].localeCompare(b[0]));

export function Modules({ path, onClose }: { path: string; onClose: () => void }) {
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

  const link = (name: string, known: boolean) =>
    known ? <a href="#" onClick={(e) => { e.preventDefault(); setModule(name); }}>{name}</a> : <span className="muted">{name} (not found)</span>;
  const f = graph?.focus;
  const names = graph ? Object.keys(graph.modules).sort().filter((n) => n.includes(filter.trim().toLowerCase())) : [];

  return (
    <div className="modal-backdrop">
      <div className="modal wide">
        <div className="row between">
          <h2>Modules{module ? `: ${module}` : ""}</h2>
          <button onClick={onClose}>Close</button>
        </div>
        {error && <div className="error" role="alert">{error}</div>}
        {!graph && !error && <p className="muted">Reading manifests…</p>}
        {graph && (
          <>
            <p className="muted">
              {Object.keys(graph.modules).length} modules in {graph.addons_paths.length} folders. Manifests are read, never imported.
            </p>
            {graph.cycles.map((c, i) => <p key={i} className="error">Cycle: {c.join(" → ")}</p>)}
            {Object.keys(graph.missing).length > 0 && (
              <p className="muted">Missing dependencies: {Object.entries(graph.missing).map(([m, d]) => `${m} needs ${d.join(", ")}`).join("; ")}</p>
            )}
            {graph.shadowed.length > 0 && (
              <p className="muted">Hidden by an earlier folder: {graph.shadowed.map((s) => s.path).join(", ")}</p>
            )}
            {graph.unreadable.length > 0 && <p className="muted">Manifest not readable: {graph.unreadable.join(", ")}</p>}
            <div className="row">
              <label className="row">State in database{" "}
                <select value={database} onChange={(e) => setDatabase(e.target.value)} disabled={dbs.length === 0}>
                  <option value="">none</option>
                  {dbs.map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
              </label>
              {dbNote && dbs.length === 0 && <span className="muted">{dbNote}</span>}
            </div>
            {graph.db_error && <p className="error">{graph.db_error}</p>}
            {graph.db_counts && (
              <p className="muted">
                {Object.entries(graph.db_counts).map(([k, v]) => `${v} ${k}`).join(", ")}
                {graph.modules && Object.values(graph.modules).some((m) => m.version_differs) &&
                  ` · ${Object.values(graph.modules).filter((m) => m.version_differs).length} installed at another version than the manifest (after a version upgrade: run -u on them)`}
              </p>
            )}
            {graph.db_only && graph.db_only.length > 0 && (
              <p className="error">In the database but not in the addons_path ({graph.db_only.length}): {graph.db_only.join(", ")}. Odoo cannot load them; add their folder below or uninstall them.</p>
            )}
            <div className="row">
              <input value={extraInput} placeholder="also look in a folder (absolute path)" size={40} onChange={(e) => setExtraInput(e.target.value)} />
              <button disabled={!extraInput.startsWith("/") || extra.includes(extraInput.trim())}
                onClick={() => { setExtra([...extra, extraInput.trim()]); setExtraInput(""); }}>Add folder</button>
              {extra.map((x) => <button key={x} onClick={() => setExtra(extra.filter((e) => e !== x))} title="Stop looking here">{x} ✕</button>)}
            </div>
            {f ? (
              <>
                <div className="row between">
                  <button onClick={() => setModule(null)}>← All modules</button>
                  <label className="row">Depth{" "}
                    <select value={depth ?? ""} onChange={(e) => setDepth(e.target.value ? Number(e.target.value) : null)}>
                      <option value="">all</option>
                      {[1, 2, 3, 4].map((d) => <option key={d} value={d}>{d}</option>)}
                    </select>
                  </label>
                </div>
                <p className="muted">{f.module.name ?? f.name} · {f.module.version ?? "no version"} · {f.module.path}{f.module.installable ? "" : " · not installable"}
                  {f.module.db_state !== undefined && ` · ${stateLabel(f.module)}${f.module.db_version ? ` ${f.module.db_version}` : ""}`}</p>
                <Picture graph={graph} focus={f} onPick={setModule} />
                <h3>Needs ({Object.keys(f.needs).length})</h3>
                <p>{byDistance(f.needs).map(([n, d]) => <span key={n}>{link(n, true)}{d > 1 ? <span className="muted"> ({d})</span> : null}{" "}</span>)}
                  {Object.keys(f.needs).length === 0 && <span className="muted">nothing</span>}</p>
                <h3>Needed by ({Object.keys(f.needed_by).length}): breaks if removed</h3>
                <p>{byDistance(f.needed_by).map(([n, d]) => <span key={n}>{link(n, true)}{d > 1 ? <span className="muted"> ({d})</span> : null}{" "}</span>)}
                  {Object.keys(f.needed_by).length === 0 && <span className="muted">nothing</span>}</p>
                {Object.entries(f.missing).map(([m, d]) => <p key={m} className="error">{m} needs {d.join(", ")}: not found in the addons_path</p>)}
              </>
            ) : (
              <>
                <input value={filter} placeholder="filter modules" onChange={(e) => setFilter(e.target.value)} />
                <table>
                  <thead><tr><th>Module</th><th>Version</th>{database && <th>In {database}</th>}<th>Depends</th><th>Required by</th></tr></thead>
                  <tbody>
                    {names.map((n) => {
                      const m = graph.modules[n];
                      return (
                        <tr key={n}>
                          <td>{link(n, true)}{!m.installable && <span className="muted"> · not installable</span>}</td>
                          <td className="muted">{m.version ?? "-"}</td>
                          {database && (
                            <td className="muted">
                              <span className={`dot ${STATE_DOT[m.db_state ?? ""] ?? ""}`} /> {stateLabel(m)}
                              {m.db_version ? ` ${m.db_version}` : ""}{m.version_differs ? " ≠" : ""}
                            </td>
                          )}
                          <td>{m.depends.length}</td>
                          <td>{m.required_by.length}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
