import { GitCompare } from "lucide-react";
import { useEffect, useState } from "react";
import { rpc } from "../rpc";
import { useApp } from "../state/app";
import { Dialog } from "../ui/Dialog";
import { Badge, Callout, Field, Loading, Segmented } from "../ui/primitives";

type Side = { path: string; name: string; notes: string[] };
type Diff = { only_a: Record<string, string>; only_b: Record<string, string>; changed: Record<string, [string, string]> };
type Result = {
  a: Side; b: Side; facts: { key: string; a: string | boolean | null; b: string | boolean | null; same: boolean }[];
  packages: Diff | null; options: Diff; addons: { only_a: string[]; only_b: string[] }; expected: string[];
};
type Mode = "configs" | "installations" | "databases";
type DbSide = { root: string; database: string };
type ModulesResult = { a: DbSide; b: DbSide; modules: Diff; counts: { a: number; b: number } };

const show = (v: string | boolean | null) => (v === null ? "–" : String(v));
const empty = (d: Diff) => !Object.keys(d.only_a).length && !Object.keys(d.only_b).length && !Object.keys(d.changed).length;

function DiffTable({ diff, title, dim }: { diff: Diff; title: string; dim?: Set<string> }) {
  if (empty(diff)) return <div className="row"><span className="section-title">{title}</span><Badge tone="ok">no difference</Badge></div>;
  const rows = [
    ...Object.entries(diff.changed).map(([k, [a, b]]) => ({ k, a, b })),
    ...Object.entries(diff.only_a).map(([k, a]) => ({ k, a, b: "–" })),
    ...Object.entries(diff.only_b).map(([k, b]) => ({ k, a: "–", b })),
  ].sort((x, y) => x.k.localeCompare(y.k));
  return (
    <div className="stack tight">
      <span className="section-title">{title} ({rows.length})</span>
      <div className="panel">
        <table>
          <thead><tr><th>Key</th><th>A</th><th>B</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.k} className={dim?.has(r.k) ? "muted" : undefined}><td className="mono small">{r.k}</td><td className="mono small break">{r.a}</td><td className="mono small break">{r.b}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Compare two configs, two installations as a whole, or the modules of two databases. */
export function CompareDialog({ path, mode: initialMode, onClose }: { path?: string; mode?: Mode; onClose: () => void }) {
  const { snap } = useApp();
  const configs = snap?.instances ?? [];
  const installations = snap?.installations ?? [];
  const databases = snap?.databases ?? [];
  const [mine, setMine] = useState(path ?? configs[0]?.path ?? "");
  const others = configs.filter((c) => c.path !== mine);
  const mineInst = configs.find((c) => c.path === mine);
  const [mode, setMode] = useState<Mode>(initialMode ?? "configs");
  const [other, setOther] = useState(others[0]?.path ?? "");
  const roots = installations.map((i) => i.root);
  const [rootA, setRootA] = useState(mineInst?.installation ?? roots[0] ?? "");
  const [rootB, setRootB] = useState(roots.find((r) => r !== (mineInst?.installation ?? roots[0])) ?? "");
  const dbOptions = databases.flatMap((e) => e.databases.map((d) => `${e.installation}|${d.name}`));
  const [dbA, setDbA] = useState(dbOptions[0] ?? "");
  const [dbB, setDbB] = useState(dbOptions[1] ?? "");
  const [result, setResult] = useState<Result | null>(null);
  const [modules, setModules] = useState<ModulesResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { if (other === mine || !other) setOther(others[0]?.path ?? ""); }, [mine]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    setResult(null); setModules(null); setError(null);
    const fail = (e: Error) => setError(String(e.message));
    const side = (v: string): DbSide => { const [root, ...rest] = v.split("|"); return { root, database: rest.join("|") }; };
    if (mode === "configs" && mine && other) rpc.request<Result>("compare.run", { path: mine, other }).then(setResult).catch(fail);
    if (mode === "installations" && rootA && rootB) rpc.request<Result>("compare.installations", { a: rootA, b: rootB }).then(setResult).catch(fail);
    if (mode === "databases" && dbA && dbB) rpc.request<ModulesResult>("compare.databases", { a: side(dbA), b: side(dbB) }).then(setModules).catch(fail);
  }, [mode, mine, other, rootA, rootB, dbA, dbB]);

  const pick = (label: string, value: string, set: (v: string) => void, options: { value: string; label: string }[]) => (
    <Field label={label}>
      <select value={value} onChange={(e) => set(e.target.value)} className="mono" style={{ fontSize: 12 }}>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </Field>
  );

  return (
    <Dialog size="xl" icon={<GitCompare />} title="Compare" subtitle="Why does it work here and not there?" onClose={onClose}>
      <Segmented label="Compare" value={mode} onChange={setMode}
        options={[{ value: "configs", label: "Configs" }, { value: "installations", label: "Installations" }, { value: "databases", label: "Databases" }]} />
      {mode === "configs" && (
        <div className="grid2">
          {pick("A", mine, setMine, configs.map((c) => ({ value: c.path, label: `${c.name} · ${c.path}` })))}
          {pick("B", other, setOther, others.map((c) => ({ value: c.path, label: `${c.name} · ${c.path}` })))}
        </div>
      )}
      {mode === "installations" && (
        <div className="grid2">
          {pick("A", rootA, setRootA, roots.map((r) => ({ value: r, label: r })))}
          {pick("B", rootB, setRootB, roots.map((r) => ({ value: r, label: r })))}
        </div>
      )}
      {mode === "databases" && (dbOptions.length < 2
        ? <Callout tone="info">Two databases are needed. Databases of installations that need an unlocked agent are listed once it is unlocked (rescan).</Callout>
        : (
          <div className="grid2">
            {pick("A", dbA, setDbA, dbOptions.map((o) => ({ value: o, label: o.replace("|", " · ") })))}
            {pick("B", dbB, setDbB, dbOptions.map((o) => ({ value: o, label: o.replace("|", " · ") })))}
          </div>
        ))}
      {error && <Callout tone="bad">{error}</Callout>}
      {!result && !modules && !error && (mode !== "databases" || dbOptions.length >= 2) && <Loading>Comparing…</Loading>}
      {modules && (
        <>
          <p className="muted small">A: {modules.counts.a} modules known, B: {modules.counts.b}. Only modules that are installed (or becoming installed) are compared.</p>
          <DiffTable diff={modules.modules} title="Modules (state and version)" />
        </>
      )}
      {result && (
        <>
          {[...result.a.notes, ...result.b.notes].map((n) => <Callout key={n} tone="info">{n}</Callout>)}
          <div className="panel">
            <table>
              <thead><tr><th /><th>A · {result.a.name}</th><th>B · {result.b.name}</th></tr></thead>
              <tbody>
                {result.facts.map((f) => (
                  <tr key={f.key} className={f.same ? "muted" : undefined}>
                    <td>{f.key}{!f.same && <Badge tone="warn">differs</Badge>}</td><td className="mono small break">{show(f.a)}</td><td className="mono small break">{show(f.b)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {mode !== "installations" && (result.addons.only_a.length + result.addons.only_b.length > 0 ? (
            <div className="stack tight">
              <span className="section-title">addons_path</span>
              <div className="panel"><table><tbody>
                {result.addons.only_a.map((e) => <tr key={"a" + e}><td className="nowrap"><Badge>only in A</Badge></td><td className="mono small">{e}</td></tr>)}
                {result.addons.only_b.map((e) => <tr key={"b" + e}><td className="nowrap"><Badge>only in B</Badge></td><td className="mono small">{e}</td></tr>)}
              </tbody></table></div>
            </div>
          ) : <div className="row"><span className="section-title">addons_path</span><Badge tone="ok">same</Badge></div>)}
          {mode !== "installations" && <DiffTable diff={result.options} title="Options" dim={new Set(result.expected)} />}
          {result.packages ? <DiffTable diff={result.packages} title="Python packages" /> : <p className="muted small">Python packages: not compared, one side has no readable venv.</p>}
          <p className="xs dim">Greyed options differ on purpose between instances (ports, database, log file). Passwords are never read.</p>
        </>
      )}
    </Dialog>
  );
}
