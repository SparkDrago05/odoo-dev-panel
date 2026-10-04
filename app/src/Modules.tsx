import { useEffect, useState } from "react";
import { rpc } from "./rpc";

type Module = {
  path: string; addons_path: string; depends: string[]; required_by: string[]; installable: boolean;
  name?: string; version?: string; application?: boolean; auto_install?: boolean | string[];
};
type Focus = { name: string; module: Module; needs: Record<string, number>; needed_by: Record<string, number>; missing: Record<string, string[]> };
type Graph = {
  modules: Record<string, Module>; missing: Record<string, string[]>; shadowed: { name: string; path: string; by: string }[];
  unreadable: string[]; cycles: string[][]; addons_paths: string[]; focus?: Focus;
};

const byDistance = (m: Record<string, number>) => Object.entries(m).sort((a, b) => a[1] - b[1] || a[0].localeCompare(b[0]));

export function Modules({ path, onClose }: { path: string; onClose: () => void }) {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [module, setModule] = useState<string | null>(null);
  const [depth, setDepth] = useState<number | null>(null);

  useEffect(() => {
    setError(null);
    rpc.request<Graph>("modules.graph", { path, module, depth })
      .then(setGraph).catch((e) => setError(String(e.message)));
  }, [path, module, depth]);

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
                <p className="muted">{f.module.name ?? f.name} · {f.module.version ?? "no version"} · {f.module.path}{f.module.installable ? "" : " · not installable"}</p>
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
                  <thead><tr><th>Module</th><th>Version</th><th>Depends</th><th>Required by</th></tr></thead>
                  <tbody>
                    {names.map((n) => {
                      const m = graph.modules[n];
                      return (
                        <tr key={n}>
                          <td>{link(n, true)}{!m.installable && <span className="muted"> · not installable</span>}</td>
                          <td className="muted">{m.version ?? "-"}</td>
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
