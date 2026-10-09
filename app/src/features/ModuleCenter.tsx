import {
  ArrowUpCircle, Boxes, CheckCircle2, Code2, Database, FilePlus2, FlaskConical, GitBranch, Network, PackagePlus, RefreshCw, Search, Trash2, XCircle,
} from "lucide-react";
import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { rpc } from "../rpc";
import { Inspector, InspectorSection } from "../shell/Chrome";
import { useApp } from "../state/app";
import type { Check, ModuleCenter, ModuleChange, ModuleInfo, ModulePlan, ModuleTestRun, Step } from "../types";
import { Dialog } from "../ui/Dialog";
import { Checks, Disclosure, type Finished, JobView, Steps, useJob } from "../ui/Job";
import { ActionMenu, ContextMenu, type MenuEntry } from "../ui/Menu";
import { ago, Badge, Callout, CheckBox, Dot, EmptyState, Field, KV, Loading, type Tone } from "../ui/primitives";
import { useRepos } from "./Repos";

type Filter = "all" | "changed" | "own" | "installed" | "differs" | "problems";
const FILTERS: [Filter, string][] = [["all", "All"], ["changed", "Changed"], ["own", "Custom"], ["installed", "Installed"], ["differs", "Version differs"], ["problems", "Problems"]];
const ACTION_LABEL: Record<string, string> = {
  install: "install", "uninstall-first": "uninstall first", upgrade: "upgrade", review: "review", reload: "reload browser", none: "no action",
};

function matches(m: ModuleInfo, f: Filter) {
  switch (f) {
    case "changed": return !!m.change;
    case "own": return !!m.own;
    case "installed": return m.db_state === "installed";
    case "differs": return !!m.version_differs;
    case "problems": return m.problems.some((p) => p.level !== "info");
    default: return true;
  }
}

function stateTone(m: ModuleInfo): Tone {
  if (m.problems.some((p) => p.level === "error")) return "bad";
  if (m.change || m.version_differs) return "warn";
  if (m.db_state === "installed") return "ok";
  return "idle";
}

function ChangeBadge({ c }: { c: ModuleChange }) {
  if (c.new) return <Badge tone="info">new</Badge>;
  if (c.removed) return <Badge tone="bad">removed</Badge>;
  if (!c.files.length) return <Badge title={`depends on changed ${c.dependency_changed.join(", ")}`}>dependency changed</Badge>;
  return <Badge tone="warn" title={c.files.map((f) => f.path).join("\n")}>{c.kinds.join(" · ")}</Badge>;
}

/** Module center of one installation: pick a config and a database; list, inspector, actions, test history. */
export function useModuleCenter({ root, selected, onSelect, enabled }: {
  root: string; selected?: string; onSelect: (name: string) => void; enabled: boolean;
}): { body: ReactNode; inspector: ReactNode } {
  const app = useApp();
  const configs = app.snap?.instances.filter((i) => i.installation === root) ?? [];
  const [config, setConfig] = useState(configs[0]?.path ?? "");
  const [database, setDatabase] = useState("");
  const [dbs, setDbs] = useState<string[]>([]);
  const [data, setData] = useState<ModuleCenter | null>(null);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<Filter>("own");
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [tests, setTests] = useState<ModuleTestRun[]>([]);
  useEffect(() => { if (!configs.some((c) => c.path === config)) setConfig(configs[0]?.path ?? ""); }, [root, configs.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const load = useCallback(async () => {
    if (!enabled || !config) return;
    setLoading(true);
    try {
      setData(await rpc.request<ModuleCenter>("modules.center", { config, database: database || null }));
    } catch (e) {
      app.onError(String((e as Error).message));
    } finally {
      setLoading(false);
    }
  }, [enabled, config, database]); // eslint-disable-line react-hooks/exhaustive-deps
  const loadTests = useCallback(() => {
    if (enabled) rpc.request<ModuleTestRun[]>("modules.tests", { installation: root }).then(setTests).catch(() => undefined);
  }, [enabled, root]);
  useEffect(() => { load(); }, [load, app.versions.modules]);
  useEffect(() => { loadTests(); }, [loadTests, app.versions.modules]);
  useEffect(() => {
    if (!enabled) return;
    rpc.request<{ databases: { name: string }[] }>("db.list", { root }).then((l) => setDbs(l.databases.map((d) => d.name))).catch(() => setDbs([]));
  }, [enabled, root]);
  useEffect(() => rpc.on("modules.finished", () => { load(); loadTests(); }), [load, loadTests]);

  const rows = useMemo(() => {
    if (!data) return [];
    const q = query.trim().toLowerCase();
    const named = Object.entries(data.modules).map(([name, m]) => ({ ...m, key: name }));
    const removed = Object.values(data.changed.modules).filter((c) => !data.modules[c.name]).map((c) => ({
      key: c.name, path: c.path, addons_path: "", depends: [], required_by: [], installable: false, problems: [], loadable: false, own: true, change: c,
    } as ModuleInfo & { key: string }));
    return [...named, ...removed].filter((m) => matches(m, filter) && (!q || m.key.includes(q) || (m.name ?? "").toLowerCase().includes(q)))
      .sort((a, b) => Number(!!b.change) - Number(!!a.change) || a.key.localeCompare(b.key));
  }, [data, filter, query]);
  const counts = useMemo(() => {
    const out: Record<string, number> = {};
    if (data) for (const [f] of FILTERS) out[f] = Object.values(data.modules).filter((m) => matches(m, f)).length + (f === "changed" || f === "all" ? Object.values(data.changed.modules).filter((c) => !data.modules[c.name]).length : 0);
    return out;
  }, [data]);
  const current = selected && data ? (data.modules[selected] ? { ...data.modules[selected], key: selected } : null) : null;
  const action = (kind: "upgrade" | "install" | "test", modules: string[]) => app.setDialog({ kind: "module-action", action: kind, config, database: database || undefined, modules });
  const open = (name: string) => app.act(`opening ${name}`, () => rpc.request("modules.open", { config, module: name, target: "ide" }));
  const items = (name: string, m: ModuleInfo): MenuEntry[] => [
    { label: "Open in IDE", icon: <Code2 />, onSelect: () => open(name) },
    "sep",
    { label: "Upgrade…", icon: <ArrowUpCircle />, disabled: !database || m.db_state !== "installed" || !m.loadable, onSelect: () => action("upgrade", [name]) },
    { label: "Install…", icon: <PackagePlus />, disabled: !database || m.db_state === "installed" || !m.loadable, onSelect: () => action("install", [name]) },
    { label: "Run tests…", icon: <FlaskConical />, disabled: !m.loadable, onSelect: () => action("test", [name]) },
    "sep",
    { label: "Dependency graph", icon: <Network />, onSelect: () => app.setDialog({ kind: "modules", path: config }) },
  ];

  if (!configs.length) {
    return { body: <EmptyState icon={<Boxes />} title="No config">Modules are read through a config's addons_path.</EmptyState>, inspector: null };
  }
  const body = (
    <>
      <div className="row wrap" style={{ gap: 8, alignItems: "flex-end" }}>
        <Field label="Config"><select value={config} onChange={(e) => setConfig(e.target.value)}>{configs.map((c) => <option key={c.path} value={c.path}>{c.name}</option>)}</select></Field>
        <Field label="Database (installed state)">
          <select value={database} onChange={(e) => setDatabase(e.target.value)}>
            <option value="">none</option>
            {dbs.map((d) => <option key={d}>{d}</option>)}
          </select>
        </Field>
        <div className="grow" />
        <button className="btn ghost sm" onClick={load} disabled={loading}><RefreshCw />{loading ? "Reading…" : "Refresh"}</button>
        <button className="btn sm" onClick={() => app.setDialog({ kind: "modules", path: config })}><Network />Graph</button>
        <button className="btn sm primary" onClick={() => app.setDialog({ kind: "module-scaffold", installation: root })}><FilePlus2 />New module</button>
      </div>
      {data?.db_error && <Callout tone="warn">Installed state unavailable: {data.db_error}</Callout>}
      {data && Object.keys(data.changed.errors).length > 0 && <Callout tone="warn">Changes could not be read for: {Object.keys(data.changed.errors).join(", ")}</Callout>}
      <div className="row wrap" style={{ justifyContent: "space-between", gap: 8 }}>
        <div className="row tight wrap" role="group" aria-label="Filter">
          {FILTERS.map(([f, label]) => <button key={f} className="chip" aria-pressed={filter === f} onClick={() => setFilter(f)}>{label}<span className="count">{counts[f] ?? 0}</span></button>)}
        </div>
        <label className="search-box"><Search /><input placeholder="Filter modules" value={query} onChange={(e) => setQuery(e.target.value)} /></label>
      </div>
      {picked.size > 0 && (
        <div className="bulk-bar" role="toolbar" aria-label="Selected modules">
          <span className="strong">{picked.size} selected</span>
          <button className="btn sm" disabled={!database} title={database ? "" : "choose a database"} onClick={() => action("upgrade", [...picked])}><ArrowUpCircle />Upgrade</button>
          <button className="btn sm" disabled={!database} onClick={() => action("install", [...picked])}><PackagePlus />Install</button>
          <button className="btn sm" onClick={() => action("test", [...picked])}><FlaskConical />Test</button>
          <button className="btn ghost sm" onClick={() => setPicked(new Set())}>Clear</button>
        </div>
      )}
      {!data ? <Loading>Reading manifests and changes…</Loading> : rows.length === 0 ? (
        <p className="muted">{filter === "changed" ? "No module has uncommitted changes." : "No module matches."}</p>
      ) : (
        <div className="panel" style={{ overflow: "hidden" }}>
          <div className="list" role="listbox" aria-label="Modules" aria-multiselectable>
            {rows.slice(0, 400).map((m) => (
              <ContextMenu key={m.key} items={items(m.key, m)}>
                <div className="list-row clickable" role="option" aria-selected={selected === m.key} tabIndex={selected === m.key ? 0 : -1}
                  onClick={() => onSelect(m.key)} style={{ minHeight: 46 }}>
                  <input type="checkbox" aria-label={`Select ${m.key}`} checked={picked.has(m.key)} onClick={(e) => e.stopPropagation()}
                    onChange={(e) => setPicked((s) => { const n = new Set(s); if (e.target.checked) n.add(m.key); else n.delete(m.key); return n; })} />
                  <Dot tone={stateTone(m)} />
                  <div className="grow" style={{ display: "grid", minWidth: 0 }}>
                    <span className="strong truncate">{m.key} <span className="dim xs">{m.name && m.name !== m.key ? m.name : ""}</span></span>
                    <span className="meta mono xs truncate" title={m.path}>{m.path}</span>
                  </div>
                  <div className="row tight wrap repo-badges">
                    {m.change && <ChangeBadge c={m.change} />}
                    {m.db_state !== undefined && <Badge tone={m.db_state === "installed" ? "ok" : undefined}>{m.db_state ?? "not in db"}</Badge>}
                    {m.version_differs && <Badge tone="warn" title={`database ${m.db_version}, manifest ${m.version}`}>version differs</Badge>}
                    {!m.loadable && <Badge>not loaded</Badge>}
                    {m.problems.filter((p) => p.level !== "info").length > 0 && <Badge tone="bad">{m.problems.filter((p) => p.level !== "info").length} problem(s)</Badge>}
                  </div>
                  <div className="row-actions"><ActionMenu items={items(m.key, m)} /></div>
                </div>
              </ContextMenu>
            ))}
          </div>
        </div>
      )}
      {rows.length > 400 && <p className="xs dim">Showing 400 of {rows.length}; filter to narrow.</p>}
      <TestHistory runs={tests} onChanged={loadTests} />
    </>
  );
  const inspector = current && data && (
    <Inspector title={<span className="row tight"><Boxes style={{ width: 14, height: 14 }} />{current.key}</span>}>
      <ModuleDetails name={current.key} m={current} data={data} onPick={onSelect} database={database}
        onAction={(k) => action(k, [current.key])} onOpen={() => open(current.key)} />
    </Inspector>
  );
  return { body, inspector };
}

function ModuleDetails({ name, m, data, database, onPick, onAction, onOpen }: {
  name: string; m: ModuleInfo; data: ModuleCenter; database: string; onPick: (n: string) => void;
  onAction: (k: "upgrade" | "install" | "test") => void; onOpen: () => void;
}) {
  const chip = (n: string) => <button key={n} className="chip" onClick={() => onPick(n)}>{n}{data.missing[name]?.includes(n) ? " (missing)" : ""}</button>;
  return (
    <>
      <InspectorSection title="Module">
        <KV items={[
          ["Name", m.name, undefined],
          ["Version", m.version, "mono"],
          ["In database", m.db_state === undefined ? <span className="dim">choose a database</span> : `${m.db_state ?? "not in database"}${m.db_version ? ` · ${m.db_version}` : ""}`],
          ["Repository", m.repo ? <code className="xs break">{m.repo}</code> : <span className="dim">none</span>],
          ["Folder", <code className="xs break">{m.path}</code>],
        ]} />
      </InspectorSection>
      {m.change && (
        <InspectorSection title="Uncommitted changes">
          <Callout tone={m.change.action === "upgrade" || m.change.action === "install" ? "warn" : "info"}
            title={<>Suggested: {ACTION_LABEL[m.change.action]} <Badge>guess</Badge></>}>{m.change.why}</Callout>
          {m.change.files.length > 0 && (
            <div className="stack tight">{m.change.files.slice(0, 30).map((f) => (
              <div key={f.path} className="row tight xs"><span className="mono dim" style={{ width: 22 }}>{f.status}</span><span className="mono truncate grow">{f.path}</span><Badge>{f.kind}</Badge></div>
            ))}</div>
          )}
        </InspectorSection>
      )}
      <InspectorSection title={`Problems (${m.problems.length})`}>
        {m.problems.length === 0 ? <span className="muted small">Manifest looks fine.</span> : m.problems.map((p) => (
          <Callout key={p.code + p.text} tone={p.level === "error" ? "bad" : p.level === "warn" ? "warn" : "info"}>{p.text}</Callout>
        ))}
      </InspectorSection>
      <InspectorSection title={`Depends on (${m.depends.length})`}><div className="row tight wrap">{m.depends.map(chip)}</div></InspectorSection>
      <InspectorSection title={`Needed by (${m.required_by.length})`}><div className="row tight wrap">{m.required_by.slice(0, 40).map(chip)}</div></InspectorSection>
      <div className="action-stack">
        <button className="btn" onClick={onOpen}><Code2 />Open in IDE</button>
        <button className="btn" disabled={!database || m.db_state !== "installed" || !m.loadable} onClick={() => onAction("upgrade")}><ArrowUpCircle />Upgrade…</button>
        <button className="btn" disabled={!database || m.db_state === "installed" || !m.loadable} onClick={() => onAction("install")}><PackagePlus />Install…</button>
        <button className="btn" disabled={!m.loadable} onClick={() => onAction("test")}><FlaskConical />Run tests…</button>
      </div>
    </>
  );
}

const STATUS_TONE: Record<string, Tone> = { passed: "ok", failed: "bad", "no-tests": "warn" };

function TestHistory({ runs, onChanged }: { runs: ModuleTestRun[]; onChanged: () => void }) {
  const app = useApp();
  const job = useJob("db");
  useEffect(() => { if (job.finished) onChanged(); }, [job.finished]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!runs.length) return null;
  const drop = async (r: ModuleTestRun) => {
    if (!await app.confirm({ title: `Drop ${r.database}?`, body: <>The test database kept after the failed run. Its filestore goes to the trash folder.</>, confirm: "Drop", danger: true })) return;
    try { job.begin((await rpc.request<{ run_id: string }>("modules.drop_test", { id: r.id })).run_id); } catch (e) { app.onError(String((e as Error).message)); }
  };
  return (
    <Disclosure title={`Test runs (${runs.length})`} open={runs.some((r) => r.kept)}>
      <div className="list panel">
        {runs.slice(0, 20).map((r) => (
          <div key={r.id} className="list-row" style={{ alignItems: "flex-start", paddingTop: 6, paddingBottom: 6 }}>
            {r.status === "passed" ? <CheckCircle2 style={{ width: 14, color: "var(--success)" }} /> : <XCircle style={{ width: 14, color: r.status === "failed" ? "var(--danger)" : "var(--warning)" }} />}
            <div className="grow stack tight" style={{ minWidth: 0 }}>
              <span className="small"><span className="strong">{r.modules.join(", ")}</span> <span className="dim">· {r.tests} tests · {r.failures} failed · {r.errors} errors · {ago(r.at)}</span></span>
              {r.failed.slice(0, 5).map((f) => <span key={f.test} className="mono xs bad-text truncate">{f.kind.toUpperCase()} {f.test}</span>)}
              {r.kept && <span className="xs"><Database style={{ width: 11, height: 11 }} /> kept <code>{r.database}</code></span>}
            </div>
            <Badge tone={STATUS_TONE[r.status]}>{r.status}</Badge>
            {r.kept && <button className="btn ghost sm" onClick={() => drop(r)}><Trash2 />Drop</button>}
          </div>
        ))}
      </div>
    </Disclosure>
  );
}

type RunResult = Finished & {
  kind: string; exit_code?: number | null; backup?: string | null; status?: string; tests?: number; failures?: number; errors?: number;
  failed?: { kind: string; test: string }[]; kept?: boolean; database?: string; problems?: { title: string; level: string; count: number }[]; id?: string;
};

/** Upgrade, install or test: options, plan with the exact command, run, result. */
export function ModuleActionDialog({ action, config, database, modules, onClose }: {
  action: "upgrade" | "install" | "test"; config: string; database?: string; modules: string[]; onClose: (changed: boolean) => void;
}) {
  const [snapshot, setSnapshot] = useState(true);
  const [demo, setDemo] = useState(true);
  const [tags, setTags] = useState("");
  const [plan, setPlan] = useState<ModulePlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const job = useJob<RunResult>("modules");
  const params = useMemo(() => action === "test"
    ? { kind: "test", config, modules, tags: tags.trim() || null, demo }
    : { kind: action, config, modules, database, snapshot }, [action, config, modules, tags, demo, database, snapshot]);
  useEffect(() => {
    if (job.started) return;
    setError(null);
    const t = setTimeout(() => rpc.request<ModulePlan>("modules.plan", params).then(setPlan).catch((e) => { setPlan(null); setError(String(e.message)); }), 250);
    return () => clearTimeout(t);
  }, [params]); // eslint-disable-line react-hooks/exhaustive-deps
  const start = async () => {
    setError(null);
    try { job.begin((await rpc.request<{ run_id: string }>("modules.run", params)).run_id); } catch (e) { setError(String((e as Error).message)); }
  };
  const f = job.finished;
  const title = action === "test" ? "Run module tests" : action === "install" ? "Install modules" : "Upgrade modules";
  const icon = action === "test" ? <FlaskConical /> : action === "install" ? <PackagePlus /> : <ArrowUpCircle />;
  return (
    <Dialog size="lg" icon={icon} title={title} subtitle={`${modules.join(", ")}${database && action !== "test" ? ` · ${database}` : ""}`} onClose={() => onClose(!!f)} locked={job.busy}
      footer={<>
        <button className="btn" disabled={job.busy} onClick={() => onClose(!!f)}>{job.started ? "Close" : "Cancel"}</button>
        {!job.started && plan && <button className="btn primary" disabled={!plan.ok} onClick={start}>{icon}{plan.ok ? (action === "test" ? "Run tests" : action === "install" ? "Install" : "Upgrade") : "Fix the failed checks first"}</button>}
      </>}>
      {error && <Callout tone="bad">{error}</Callout>}
      {!job.started && (
        <>
          {action === "test" ? (
            <>
              <Callout tone="info">Tests run in a new database ({plan?.database ?? "odp_test_…"}) created by Odoo; your databases are never touched. It is dropped if every test passes and kept if not, for inspection.</Callout>
              <Field label="Test tags" hint="Empty: only these modules' tests (/module). Examples: /adm:TestApplicant, -slow, post_install">
                <input className="mono" value={tags} placeholder={modules.map((m) => `/${m}`).join(",")} onChange={(e) => setTags(e.target.value)} />
              </Field>
              <CheckBox checked={demo} onChange={setDemo}>Load demo data (most Odoo tests expect it)</CheckBox>
            </>
          ) : (
            <>
              <Callout tone="warn">{action === "install" ? "-i" : "-u"} runs once with --stop-after-init in <b>{database}</b>. Upgrades change data; a snapshot lets you go back.</Callout>
              <CheckBox checked={snapshot} onChange={setSnapshot}>Snapshot {database} first (database and filestore)</CheckBox>
            </>
          )}
          {!plan && !error && <Loading />}
          {plan && <><Checks checks={plan.checks as Check[]} /><Steps steps={plan.steps as Step[]} actors /></>}
        </>
      )}
      {job.started && (
        <JobView job={job} done={f?.kind === "test" ? (f.status === "passed" ? "All tests passed." : "Tests finished with failures.") : f && f.exit_code === 0 ? "Done." : "Finished with an error."}>
          {f?.ok && f.kind === "test" && (
            <Callout tone={f.status === "passed" ? "ok" : f.status === "no-tests" ? "warn" : "bad"}
              title={`${f.status}: ${f.tests} tests, ${f.failures} failed, ${f.errors} errors`}>
              {f.kept ? <>The database <code>{f.database}</code> was kept for inspection; drop it from the test runs list.</> : "The test database was dropped."}
              {(f.failed ?? []).slice(0, 10).map((x) => <div key={x.test} className="mono xs">{x.kind.toUpperCase()} {x.test}</div>)}
            </Callout>
          )}
          {f?.ok && f.kind !== "test" && (
            <Callout tone={f.exit_code === 0 ? "ok" : "bad"} title={`odoo-bin exited with ${f.exit_code}`}>
              {f.backup && <>Snapshot: <code>{f.backup}</code>. </>}
              {(f.problems ?? []).slice(0, 5).map((p) => <div key={p.title} className="xs">{p.level} ×{p.count}: {p.title}</div>)}
            </Callout>
          )}
        </JobView>
      )}
    </Dialog>
  );
}

type ScaffoldPlan = { target: string; files: Record<string, string>; ok: boolean; checks: Check[] };

/** New module from the built-in minimal template, in a folder of the installation or one of its repositories. */
export function ScaffoldDialog({ installation, onClose }: { installation: string; onClose: (changed: boolean) => void }) {
  const app = useApp();
  const { repos } = useRepos(installation);
  const [folder, setFolder] = useState("");
  const [name, setName] = useState("");
  const [depends, setDepends] = useState("base");
  const [plan, setPlan] = useState<ScaffoldPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const folders = useMemo(() => (repos ?? []).filter((r) => r.purpose !== "community" && r.purpose !== "enterprise").map((r) => r.path), [repos]);
  useEffect(() => { if (!folder && folders.length) setFolder(folders[0]); }, [folders]); // eslint-disable-line react-hooks/exhaustive-deps
  const params = useMemo(() => ({ installation, folder, name: name.trim(), depends: depends.split(",").map((d) => d.trim()).filter(Boolean) }), [installation, folder, name, depends]);
  useEffect(() => {
    setPlan(null); setError(null);
    if (!folder || !/^[a-z][a-z0-9_]{1,62}$/.test(params.name)) return;
    const t = setTimeout(() => rpc.request<ScaffoldPlan>("modules.scaffold_plan", params).then(setPlan).catch((e) => setError(String(e.message))), 250);
    return () => clearTimeout(t);
  }, [params]); // eslint-disable-line react-hooks/exhaustive-deps
  const create = async () => {
    if (await app.act("creating the module", () => rpc.request("modules.scaffold", params), `Created ${plan?.target}`)) { app.invalidate("modules"); onClose(true); }
  };
  return (
    <Dialog size="lg" icon={<FilePlus2 />} title="New module" subtitle={installation} onClose={() => onClose(false)}
      footer={<><button className="btn" onClick={() => onClose(false)}>Cancel</button><button className="btn primary" disabled={!plan?.ok} onClick={create}><FilePlus2 />Create</button></>}>
      {error && <Callout tone="bad">{error}</Callout>}
      <Field label="Folder" hint="A repository of this installation, or type another folder inside the installation">
        <input className="mono" list="scaffold-folders" value={folder} onChange={(e) => setFolder(e.target.value)} />
        <datalist id="scaffold-folders">{folders.map((f) => <option key={f} value={f} />)}</datalist>
      </Field>
      <div className="grid2">
        <Field label="Technical name" hint="lowercase, digits, _"><input autoFocus className="mono" value={name} onChange={(e) => setName(e.target.value.toLowerCase())} placeholder="edu_attendance" /></Field>
        <Field label="Depends" hint="comma-separated"><input className="mono" value={depends} onChange={(e) => setDepends(e.target.value)} /></Field>
      </div>
      {plan && (
        <>
          <Checks checks={plan.checks} />
          <span className="section-title"><GitBranch style={{ width: 12, height: 12 }} /> {plan.target}</span>
          {Object.entries(plan.files).map(([rel, text]) => <Disclosure key={rel} title={<span className="mono xs">{rel}</span>} open={rel === "__manifest__.py"}><pre className="block">{text}</pre></Disclosure>)}
        </>
      )}
    </Dialog>
  );
}
