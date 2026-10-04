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

type Mode = "configs" | "installations" | "databases";
type DbSide = { root: string; database: string };
type ModulesResult = { a: DbSide; b: DbSide; modules: Diff; counts: { a: number; b: number } };

/** Compare two configs, two installations as a whole, or the modules of two databases. */
export function Compare({ path, configs, installations, databases, onClose }: {
  path: string; configs: { path: string; name: string; installation: string | null }[];
  installations: { root: string }[]; databases: { installation: string; databases: { name: string }[] }[];
  onClose: () => void;
}) {
  const others = configs.filter((c) => c.path !== path);
  const mine = configs.find((c) => c.path === path);
  const [mode, setMode] = useState<Mode>("configs");
  const [other, setOther] = useState(others[0]?.path ?? "");
  const roots = installations.map((i) => i.root);
  const [rootA, setRootA] = useState(mine?.installation ?? roots[0] ?? "");
  const [rootB, setRootB] = useState(roots.find((r) => r !== (mine?.installation ?? roots[0])) ?? "");
  const dbOptions = databases.flatMap((e) => e.databases.map((d) => `${e.installation}|${d.name}`));
  const [dbA, setDbA] = useState(dbOptions[0] ?? "");
  const [dbB, setDbB] = useState(dbOptions[1] ?? "");
  const [result, setResult] = useState<Result | null>(null);
  const [modules, setModules] = useState<ModulesResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setResult(null); setModules(null); setError(null);
    const fail = (e: Error) => setError(String(e.message));
    const side = (v: string): DbSide => { const [root, ...rest] = v.split("|"); return { root, database: rest.join("|") }; };
    if (mode === "configs" && other) rpc.request<Result>("compare.run", { path, other }).then(setResult).catch(fail);
    if (mode === "installations" && rootA && rootB) rpc.request<Result>("compare.installations", { a: rootA, b: rootB }).then(setResult).catch(fail);
    if (mode === "databases" && dbA && dbB) rpc.request<ModulesResult>("compare.databases", { a: side(dbA), b: side(dbB) }).then(setModules).catch(fail);
  }, [mode, path, other, rootA, rootB, dbA, dbB]);

  return (
    <div className="modal-backdrop">
      <div className="modal wide">
        <div className="row between">
          <h2>Compare</h2>
          <button onClick={onClose}>Close</button>
        </div>
        <div className="row">
          {(["configs", "installations", "databases"] as Mode[]).map((m) => (
            <button key={m} className={mode === m ? "primary" : ""} onClick={() => setMode(m)}>
              {{ configs: "Configs", installations: "Installations", databases: "Databases" }[m]}
            </button>
          ))}
        </div>
        {mode === "configs" && (
          <div className="row">
            <span>{mine?.name ?? path} <span className="muted">(A)</span> with</span>
            <select value={other} onChange={(e) => setOther(e.target.value)}>
              {others.map((c) => <option key={c.path} value={c.path}>{c.name} · {c.path}</option>)}
            </select>
          </div>
        )}
        {mode === "installations" && (
          <div className="row">
            <select value={rootA} onChange={(e) => setRootA(e.target.value)} aria-label="Installation A">{roots.map((r) => <option key={r}>{r}</option>)}</select>
            <span className="muted">with</span>
            <select value={rootB} onChange={(e) => setRootB(e.target.value)} aria-label="Installation B">{roots.map((r) => <option key={r}>{r}</option>)}</select>
          </div>
        )}
        {mode === "databases" && (
          dbOptions.length < 2 ? <p className="muted">Two databases are needed. Databases of installations that need an unlocked agent are listed once it is unlocked (rescan).</p> : (
            <div className="row">
              <select value={dbA} onChange={(e) => setDbA(e.target.value)} aria-label="Database A">{dbOptions.map((o) => <option key={o} value={o}>{o.replace("|", " · ")}</option>)}</select>
              <span className="muted">with</span>
              <select value={dbB} onChange={(e) => setDbB(e.target.value)} aria-label="Database B">{dbOptions.map((o) => <option key={o} value={o}>{o.replace("|", " · ")}</option>)}</select>
            </div>
          )
        )}
        {error && <div className="error" role="alert">{error}</div>}
        {!result && !modules && !error && <p className="muted">Reading…</p>}
        {modules && (
          <>
            <p className="muted">
              A: {modules.counts.a} modules known, B: {modules.counts.b}. Only modules that are installed (or becoming installed) are compared.
            </p>
            <DiffTable diff={modules.modules} title="Modules (A / B: state and version)" />
          </>
        )}
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
            {mode === "installations" ? null : result.addons.only_a.length + result.addons.only_b.length > 0 ? (
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
            {mode !== "installations" && <DiffTable diff={result.options} title="Options" dim={new Set(result.expected)} />}
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
