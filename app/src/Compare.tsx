import { useEffect, useState } from "react";
import { rpc } from "./rpc";

type Side = { path: string; name: string; notes: string[] };
type Diff = { only_a: Record<string, string>; only_b: Record<string, string>; changed: Record<string, [string, string]> };
type Result = {
  a: Side; b: Side; facts: { key: string; a: string | boolean | null; b: string | boolean | null; same: boolean }[];
  packages: Diff | null; options: Diff; addons: { only_a: string[]; only_b: string[] }; expected: string[];
};

const show = (v: string | boolean | null) => (v === null ? "-" : String(v));
const empty = (d: Diff) => !Object.keys(d.only_a).length && !Object.keys(d.only_b).length && !Object.keys(d.changed).length;

function DiffTable({ diff, title, dim }: { diff: Diff; title: string; dim?: Set<string> }) {
  if (empty(diff)) return <p className="muted">{title}: no difference.</p>;
  const rows = [
    ...Object.entries(diff.changed).map(([k, [a, b]]) => ({ k, a, b })),
    ...Object.entries(diff.only_a).map(([k, a]) => ({ k, a, b: "-" })),
    ...Object.entries(diff.only_b).map(([k, b]) => ({ k, a: "-", b })),
  ].sort((x, y) => x.k.localeCompare(y.k));
  return (
    <>
      <h3>{title} ({rows.length})</h3>
      <table>
        <tbody>
          {rows.map((r) => (
            <tr key={r.k} className={dim?.has(r.k) ? "muted" : undefined}><td>{r.k}</td><td>{r.a}</td><td>{r.b}</td></tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

export function Compare({ path, configs, onClose }: { path: string; configs: { path: string; name: string }[]; onClose: () => void }) {
  const others = configs.filter((c) => c.path !== path);
  const [other, setOther] = useState(others[0]?.path ?? "");
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!other) return;
    setResult(null); setError(null);
    rpc.request<Result>("compare.run", { path, other }).then(setResult).catch((e) => setError(String(e.message)));
  }, [path, other]);

  return (
    <div className="modal-backdrop">
      <div className="modal wide">
        <div className="row between">
          <h2>Compare</h2>
          <button onClick={onClose}>Close</button>
        </div>
        <div className="row">
          <span>{configs.find((c) => c.path === path)?.name ?? path} <span className="muted">(A)</span> with</span>
          <select value={other} onChange={(e) => setOther(e.target.value)}>
            {others.map((c) => <option key={c.path} value={c.path}>{c.name} · {c.path}</option>)}
          </select>
        </div>
        {error && <div className="error" role="alert">{error}</div>}
        {!result && !error && other && <p className="muted">Reading…</p>}
        {result && (
          <>
            {[...result.a.notes, ...result.b.notes].map((n) => <p key={n} className="muted">{n}</p>)}
            <table>
              <thead><tr><th /><th>A: {result.a.name}</th><th>B: {result.b.name}</th></tr></thead>
              <tbody>
                {result.facts.map((f) => (
                  <tr key={f.key} className={f.same ? "muted" : undefined}>
                    <td>{f.key}{f.same ? "" : " ≠"}</td><td>{show(f.a)}</td><td>{show(f.b)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {result.addons.only_a.length + result.addons.only_b.length > 0 ? (
              <>
                <h3>addons_path</h3>
                <table>
                  <tbody>
                    {result.addons.only_a.map((e) => <tr key={"a" + e}><td>only in A</td><td>{e}</td></tr>)}
                    {result.addons.only_b.map((e) => <tr key={"b" + e}><td>only in B</td><td>{e}</td></tr>)}
                  </tbody>
                </table>
              </>
            ) : <p className="muted">addons_path: same.</p>}
            <DiffTable diff={result.options} title="Options" dim={new Set(result.expected)} />
            {result.packages
              ? <DiffTable diff={result.packages} title="Python packages" />
              : <p className="muted">Python packages: not compared, one side has no readable venv.</p>}
            <p className="muted">Greyed options differ on purpose between instances (ports, database, log file). Passwords are never read.</p>
          </>
        )}
      </div>
    </div>
  );
}
