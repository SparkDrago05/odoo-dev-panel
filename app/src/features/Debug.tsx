import { Bug, ClipboardCopy, FileJson, FlaskConical, Pencil, Play, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { rpc } from "../rpc";
import { sessionKey, useApp } from "../state/app";
import type { Check } from "../types";
import { Dialog } from "../ui/Dialog";
import { Checks, Disclosure, type Finished, JobView, useJob } from "../ui/Job";
import { LogConsole } from "../ui/LogConsole";
import { ProblemList, type ProblemGroup, SqlList, type SqlSummary } from "../views/Problems";
import { ago, Badge, Callout, CheckBox, Cmd, CopyButton, Dot, EmptyState, Field, Loading, Segmented } from "../ui/primitives";
import { Panel } from "../views/common";

export type Preset = {
  id: string; name: string; instance: string; kind: "server" | "test"; database: string | null; http_port: number | null;
  dev: string[]; update: string[]; install: string[]; modules: string[]; tags: string | null; demo: boolean; port: number; wait: boolean;
};
type DebugPlan = {
  kind: "server" | "test"; preset: Preset; ok: boolean; checks: Check[]; commands: string[]; user: string;
  attach: { host: string; port: number; wait: boolean }; entry: Record<string, unknown>;
};
type VscodePlan = {
  path: string; exists: boolean; added: string[]; replaced: string[]; unchanged: string[]; conflicts: string[]; kept: number;
  checks: Check[]; diff: string; snippet: string; ok: boolean; jsonc: boolean;
};

const DEV_FLAGS = ["xml", "qweb", "werkzeug", "reload"];
const csv = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "preset";

/** Q1-Q4: the Debug tab of an instance: presets, start under debugpy, VS Code attach entries, debug sessions. */
export function DebugPanel({ path, root }: { path: string; root: string | null }) {
  const app = useApp();
  const [rows, setRows] = useState<Preset[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const r = await rpc.request<{ presets: Preset[]; error: string | null }>("debug.presets", { instance: path });
      setRows(r.presets); setError(r.error);
    } catch (e) { setError(String((e as Error).message)); }
  }, [path]);
  useEffect(() => { load(); }, [load, app.versions.debug]);
  const ids = useMemo(() => new Set((rows ?? []).map((r) => r.id)), [rows]);
  const sessions = app.sessions.filter((s) => s.meta?.instance === path && (s.meta?.debug || ids.has(String(s.meta?.preset ?? "")))).slice(0, 10);

  const remove = async (p: Preset) => {
    if (!(await app.confirm({ title: `Delete preset ${p.name}?`, body: "The previous preset file is kept as debug-presets.json.bak-TIME. An entry already in launch.json stays.", confirm: "Delete", danger: true }))) return;
    try { await rpc.request("debug.delete", { id: p.id }); app.invalidate("debug"); } catch (e) { app.onError(String((e as Error).message)); }
  };

  return (
    <>
      <Callout tone="info">
        A preset starts Odoo under <code>debugpy</code> as the run-as user, on a fixed port of 127.0.0.1, so VS Code attaches with one saved entry.
        Debug runs use <code>--workers=0</code> so breakpoints in requests hit. While it listens, any local user can connect to that port.
      </Callout>
      {error && <Callout tone="bad">{error}</Callout>}
      <Panel flush title="Debug presets" actions={<div className="row tight">
        <button className="btn ghost sm" onClick={load}><RefreshCw />Refresh</button>
        {root && <button className="btn sm" disabled={!rows?.length} onClick={() => app.setDialog({ kind: "debug-vscode", root })}><FileJson />VS Code launch.json…</button>}
        <button className="btn sm primary" onClick={() => app.setDialog({ kind: "debug-preset", instance: path })}><Plus />New preset</button>
      </div>}>
        {!rows ? <Loading /> : rows.length === 0 ? (
          <EmptyState icon={<Bug />} title="No debug preset for this instance"
            actions={<button className="btn primary" onClick={() => app.setDialog({ kind: "debug-preset", instance: path })}><Plus />New preset</button>}>
            A preset remembers the database, flags and the debugpy port of a debug run, or modules to test under the debugger.
          </EmptyState>
        ) : (
          <div className="list">
            {rows.map((p) => (
              <div key={p.id} className="list-row">
                {p.kind === "test" ? <FlaskConical style={{ width: 15 }} /> : <Bug style={{ width: 15 }} />}
                <div className="stack tight grow" style={{ minWidth: 0, gap: 2 }}>
                  <span className="row tight"><span className="strong truncate">{p.name}</span><Badge mono>:{p.port}</Badge>{p.wait && <Badge tone="warn">waits for IDE</Badge>}</span>
                  <span className="xs dim truncate">
                    {p.kind === "test" ? `tests of ${p.modules.join(", ")}${p.tags ? ` (${p.tags})` : ""} in a throwaway database`
                      : `${p.database ?? "database from the config"}${p.update.length ? ` · -u ${p.update.join(",")}` : ""}${p.install.length ? ` · -i ${p.install.join(",")}` : ""}${p.dev.length ? ` · --dev=${p.dev.join(",")}` : ""}`}
                  </span>
                </div>
                <button className="btn ghost sm" title="Delete" aria-label={`Delete ${p.name}`} onClick={() => remove(p)}><Trash2 /></button>
                <button className="btn ghost sm" onClick={() => app.setDialog({ kind: "debug-preset", instance: path, id: p.id })}><Pencil />Edit</button>
                <button className="btn sm primary" onClick={() => app.setDialog({ kind: "debug-start", id: p.id })}><Play />{p.kind === "test" ? "Test…" : "Debug…"}</button>
              </div>
            ))}
          </div>
        )}
      </Panel>
      <Panel flush title="Debug sessions">
        {sessions.length === 0 ? <p className="muted" style={{ padding: 14 }}>No debug session yet.</p> : (
          <div className="list">
            {sessions.map((s) => {
              const dbg = s.meta?.debug;
              return (
                <div key={sessionKey(s)} className="list-row clickable" onClick={() => { app.follow(s); app.nav({ view: "sessions", key: sessionKey(s) }); }}>
                  <Dot tone={s.state === "running" ? "ok" : s.exit_code ? "bad" : "idle"} live={s.state === "running"} />
                  <span className="grow truncate">{s.name}</span>
                  {dbg?.port && <Badge mono>debugpy :{dbg.port}</Badge>}
                  {s.meta?.kind && <Badge>{s.meta.kind}</Badge>}
                  <span className="meta xs mono nowrap">{s.state === "running" ? "running" : `exit ${s.exit_code ?? "?"}`} · {ago(s.started_at)}</span>
                </div>
              );
            })}
          </div>
        )}
      </Panel>
    </>
  );
}

/** New or edited preset. The debug port is stable per preset (VS Code's entry uses it). */
export function PresetDialog({ instance, id, onClose }: { instance: string; id?: string; onClose: (saved: boolean) => void }) {
  const app = useApp();
  const inst = app.snap?.instances.find((i) => i.path === instance);
  const dbs = app.snap?.databases.find((d) => d.installation === inst?.installation)?.databases ?? [];
  const [p, setP] = useState<Preset | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    (async () => {
      try {
        if (id) {
          const r = await rpc.request<{ presets: Preset[] }>("debug.presets", {});
          const found = r.presets.find((x) => x.id === id);
          if (!found) throw new Error(`no preset ${id}`);
          setP(found);
        } else {
          const { port } = await rpc.request<{ port: number }>("debug.next_port");
          setP({ id: "", name: `${inst?.name ?? "odoo"} debug`, instance, kind: "server", database: null, http_port: null, dev: ["xml"],
            update: [], install: [], modules: [], tags: null, demo: true, port, wait: false });
        }
      } catch (e) { setError(String((e as Error).message)); }
    })();
  }, [id, instance, inst?.name]);
  const set = (patch: Partial<Preset>) => setP((old) => (old ? { ...old, ...patch } : old));
  const save = async () => {
    if (!p) return;
    setSaving(true); setError(null);
    try {
      await rpc.request("debug.save", { preset: { ...p, id: p.id || slug(p.name) }, overwrite: !!id });
      onClose(true);
    } catch (e) { setError(String((e as Error).message)); } finally { setSaving(false); }
  };
  return (
    <Dialog size="lg" icon={<Bug />} title={id ? `Edit ${p?.name ?? id}` : "New debug preset"} subtitle={instance} onClose={() => onClose(false)}
      footer={<>
        <button className="btn" onClick={() => onClose(false)}>Cancel</button>
        <button className="btn primary" disabled={!p || saving || !p.name.trim()} onClick={save}>{saving ? "Saving…" : "Save"}</button>
      </>}>
      {error && <Callout tone="bad">{error}</Callout>}
      {!p ? (!error && <Loading />) : (
        <div className="stack">
          <div className="grid2">
            <Field label="Name" hint={id ? `id ${p.id}` : `id ${slug(p.name)}`}><input value={p.name} onChange={(e) => set({ name: e.target.value })} autoFocus /></Field>
            <Field label="Run">
              <Segmented label="Kind" value={p.kind} onChange={(kind) => set({ kind })} options={[{ value: "server", label: "Server" }, { value: "test", label: "Module tests" }]} />
            </Field>
          </div>
          {p.kind === "server" ? (
            <>
              <div className="grid3">
                <Field label="Database" hint="empty: from the config">
                  <input list="dbg-dbs" className="mono" value={p.database ?? ""} onChange={(e) => set({ database: e.target.value || null })} />
                </Field>
                <datalist id="dbg-dbs">{dbs.map((d) => <option key={d.name} value={d.name} />)}</datalist>
                <Field label="HTTP port" hint="empty: first free">
                  <input className="mono" value={p.http_port ?? ""} onChange={(e) => set({ http_port: e.target.value ? Number(e.target.value.replace(/\D/g, "")) : null })} />
                </Field>
                <Field label="debugpy port" hint="stable; VS Code attaches here">
                  <input className="mono" value={p.port} onChange={(e) => set({ port: Number(e.target.value.replace(/\D/g, "")) || 0 })} />
                </Field>
              </div>
              <div className="grid2">
                <Field label={<>Update <code className="dim">-u</code></>} hint="comma separated; needs a database">
                  <input className="mono" value={p.update.join(", ")} onChange={(e) => set({ update: csv(e.target.value) })} />
                </Field>
                <Field label={<>Install <code className="dim">-i</code></>} hint="comma separated; needs a database">
                  <input className="mono" value={p.install.join(", ")} onChange={(e) => set({ install: csv(e.target.value) })} />
                </Field>
              </div>
              <Field label={<code>--dev</code>} hint="reload restarts Odoo outside the debugger">
                <div className="row tight">
                  {DEV_FLAGS.map((f) => (
                    <button key={f} type="button" className="chip" aria-pressed={p.dev.includes(f)}
                      onClick={() => set({ dev: p.dev.includes(f) ? p.dev.filter((x) => x !== f) : [...p.dev, f] })}>{f}</button>
                  ))}
                </div>
              </Field>
            </>
          ) : (
            <div className="grid3">
              <Field label="Modules" hint="comma separated"><input className="mono" value={p.modules.join(", ")} onChange={(e) => set({ modules: csv(e.target.value) })} /></Field>
              <Field label={<code>--test-tags</code>} hint="optional"><input className="mono" value={p.tags ?? ""} placeholder="/sale_x:TestFoo" onChange={(e) => set({ tags: e.target.value || null })} /></Field>
              <Field label="debugpy port"><input className="mono" value={p.port} onChange={(e) => set({ port: Number(e.target.value.replace(/\D/g, "")) || 0 })} /></Field>
            </div>
          )}
          <div className="row">
            <CheckBox checked={p.wait} onChange={(wait) => set({ wait })}>Wait for the IDE to attach before Odoo starts</CheckBox>
            {p.kind === "test" && <CheckBox checked={p.demo} onChange={(demo) => set({ demo })}>Demo data</CheckBox>}
          </div>
          {p.kind === "test" && <span className="small muted">Tests run in a new odp_test_* database, dropped when they pass and kept when they fail, like the module center.</span>}
        </div>
      )}
    </Dialog>
  );
}

type TestFinished = Finished & { kind?: string; test?: { status: string; tests: number; failures: number; errors: number; database: string; kept: boolean; id: string; failed: { kind: string; test: string }[] } };

/** Plan of a preset (checks, command, attach entry), then start it. */
export function DebugStartDialog({ id, onClose }: { id: string; onClose: (started: boolean) => void }) {
  const app = useApp();
  const [plan, setPlan] = useState<DebugPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [started, setStarted] = useState<string | null>(null);
  const job = useJob<TestFinished>("debug");
  const review = useCallback(() => {
    setError(null);
    rpc.request<DebugPlan>("debug.plan", { id }).then(setPlan).catch((e) => setError(String(e.message)));
  }, [id]);
  useEffect(review, [review]);
  const start = async () => {
    if (!plan) return;
    setStarting(true); setError(null);
    try {
      const r = await rpc.request<{ kind: string; run_id?: string; session?: { id: string } }>("debug.start", { id });
      if (r.kind === "test" && r.run_id) job.begin(r.run_id);
      else if (r.session) { await app.startSession(plan.user, r.session.id); setStarted(r.session.id); }
    } catch (e) { setError(String((e as Error).message)); } finally { setStarting(false); }
  };
  const entry = plan ? JSON.stringify(plan.entry, null, 4) : "";
  const t = job.finished?.test;
  return (
    <Dialog size="lg" icon={<Bug />} title={plan ? `${plan.kind === "test" ? "Test" : "Debug"}: ${plan.preset.name}` : "Debug"} subtitle={plan?.preset.instance}
      onClose={() => onClose(!!started || job.started)} locked={job.busy}
      footer={<>
        <button className="btn" disabled={job.busy} onClick={() => onClose(!!started || job.started)}>{started || job.started ? "Close" : "Cancel"}</button>
        {!started && !job.started && plan && <button className="btn ghost" onClick={review}><RefreshCw />Check again</button>}
        {!started && !job.started && plan && <button className="btn primary" disabled={!plan.ok || starting} onClick={start}><Play />{starting ? "Starting…" : plan.ok ? "Start under debugpy" : "Fix the failed checks first"}</button>}
      </>}>
      {error && <Callout tone="bad">{error}</Callout>}
      {!plan && !error && <Loading />}
      {plan && (
        <div className="stack">
          {started && (
            <Callout tone="ok" title={`Running as ${plan.user}; debugpy listens on 127.0.0.1:${plan.attach.port}`}>
              In VS Code, start the debug configuration <strong>{String(plan.entry.name)}</strong>{plan.attach.wait ? "; Odoo starts once it is attached" : ""}. Stop the session to close the port. Output is in Sessions.
            </Callout>
          )}
          {!job.started && (
            <>
              <Checks checks={plan.checks} />
              <Field label={<>Command <span className="dim">· runs as {plan.user} through its agent</span></>}>
                <div className="stack tight">{plan.commands.map((c) => <Cmd key={c}>{c}</Cmd>)}</div>
              </Field>
              <Disclosure title={<span className="row tight">VS Code attach entry <CopyButton text={entry} label="Copy" /></span>} open={!!started}>
                <pre className="block">{entry}</pre>
              </Disclosure>
            </>
          )}
          {job.started && (
            <JobView job={{ ...job, finished: job.finished && { ...job.finished, ok: job.finished.ok && (!t || t.status === "passed") } }} running={`Tests run under debugpy on 127.0.0.1:${plan.attach.port}${plan.attach.wait ? " (waiting for the IDE)" : ""}…`}
              done={t ? `${t.status}: ${t.tests} tests, ${t.failures} failed, ${t.errors} errors` : "Done."}>
              {t && t.kept && <Callout tone="warn">The test database {t.database} was kept for inspection. Drop it from the module center's test history.</Callout>}
              {t?.failed.map((f) => <div key={f.test} className="mono xs bad-text">{f.kind.toUpperCase()} {f.test}</div>)}
            </JobView>
          )}
        </div>
      )}
    </Dialog>
  );
}

/** Q1: write the installation's attach entries into <root>/.vscode/launch.json, diff first. */
export function VscodeDialog({ root, onClose }: { root: string; onClose: () => void }) {
  const app = useApp();
  const [replace, setReplace] = useState(false);
  const [plan, setPlan] = useState<VscodePlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  useEffect(() => {
    setPlan(null);
    rpc.request<VscodePlan>("debug.vscode_plan", { root, replace }).then(setPlan).catch((e) => setError(String(e.message)));
  }, [root, replace]);
  const write = async () => {
    try {
      const r = await rpc.request<{ path: string; backup: string | null; changed: boolean }>("debug.vscode_write", { root, replace });
      setDone(r.changed ? `Wrote ${r.path}${r.backup ? `; the previous file is ${r.backup}` : ""}.` : "Nothing to change.");
      app.notify("ok", r.changed ? "launch.json updated" : "launch.json already up to date");
    } catch (e) { setError(String((e as Error).message)); }
  };
  const nothing = plan && !plan.added.length && !plan.replaced.length;
  return (
    <Dialog size="lg" icon={<FileJson />} title="VS Code attach entries" subtitle={`${root}/.vscode/launch.json`} onClose={onClose}
      footer={<>
        <button className="btn" onClick={onClose}>{done ? "Close" : "Cancel"}</button>
        {!done && plan && <button className="btn primary" disabled={!plan.ok || !!nothing} onClick={write}>{nothing ? "Up to date" : "Write launch.json"}</button>}
      </>}>
      {error && <Callout tone="bad">{error}</Callout>}
      {done && <Callout tone="ok">{done} Open {root} as the VS Code workspace to use the entries.</Callout>}
      {!plan && !error && <Loading />}
      {plan && !done && (
        <div className="stack">
          <Checks checks={plan.checks} />
          {plan.conflicts.length > 0 && <CheckBox checked={replace} onChange={setReplace}>Replace {plan.conflicts.join(", ")} (the old entry is in the backup)</CheckBox>}
          {plan.diff && <Disclosure title="Changes" open><pre className="block diff-pre">{plan.diff}</pre></Disclosure>}
          {!plan.ok && (
            <Disclosure title={<span className="row tight"><ClipboardCopy style={{ width: 13 }} />Entries to paste <CopyButton text={plan.snippet} /></span>} open={plan.jsonc}>
              <pre className="block">{plan.snippet}</pre>
            </Disclosure>
          )}
        </div>
      )}
    </Dialog>
  );
}

type ExtLog = { source: { kind: "logfile" | "journal" | null; path: string | null; unit: string | null; reason: string | null };
  text: string; offset: number | null; rotated: boolean; analysis?: { groups: ProblemGroup[] }; sql?: SqlSummary };

/** U8: the log of an instance started outside the app: its config's logfile (followed) or its unit's journal. */
export function ExternalLog({ path }: { path: string }) {
  const [log, setLog] = useState<ExtLog | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"log" | "problems" | "sql">("log");
  const load = useCallback(async () => {
    setError(null);
    try { const r = await rpc.request<ExtLog>("instance.logs", { path }); setLog(r); setText(r.text); }
    catch (e) { setError(String((e as Error).message)); }
  }, [path]);
  useEffect(() => { load(); }, [load]);
  const offset = log?.offset;
  useEffect(() => {
    if (log?.source.kind !== "logfile" || offset == null) return;
    let at = offset;
    const t = setInterval(async () => {
      try {
        const r = await rpc.request<ExtLog>("instance.logs", { path, offset: at });
        at = r.offset ?? at;
        if (r.rotated) setText(r.text); else if (r.text) setText((old) => (old + r.text).slice(-2_000_000));
      } catch { /* the next poll tries again */ }
    }, 2000);
    return () => clearInterval(t);
  }, [path, log?.source.kind, offset]);
  if (error) return <Callout tone="bad">{error}</Callout>;
  if (!log) return <Loading>Reading the log…</Loading>;
  if (!log.source.kind) return <Callout tone="info" title="No log to show">{log.source.reason}</Callout>;
  const groups = log.analysis?.groups ?? [];
  return (
    <Panel flush title={<div className="row tight"><h2>Log</h2><span className="xs dim mono">{log.source.kind === "logfile" ? log.source.path : `journal of ${log.source.unit}`}</span></div>}
      actions={<div className="row tight">
        <Segmented label="Show" value={view} onChange={setView} options={[{ value: "log", label: "Log" }, { value: "problems", label: `Problems (${groups.length})` }, ...(log.sql?.queries ? [{ value: "sql" as const, label: `SQL (${log.sql.queries})` }] : [])]} />
        <button className="btn ghost sm" onClick={load}><RefreshCw />Reload</button>
      </div>}>
      {view === "log" ? <div style={{ height: 420 }}><LogConsole text={text} empty="The log is empty." /></div>
        : view === "sql" && log.sql ? <SqlList sql={log.sql} /> : <ProblemList groups={groups} />}
    </Panel>
  );
}
