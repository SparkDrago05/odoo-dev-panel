import { CheckCircle2, Circle, FileCode2, ListChecks, Pause, Play, RotateCcw, Save, SkipForward, Square, XCircle } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { rpc } from "../rpc";
import { useApp } from "../state/app";
import type { Check } from "../types";
import { Dialog } from "../ui/Dialog";
import { Checks, Disclosure } from "../ui/Job";
import { LogConsole } from "../ui/LogConsole";
import { Badge, Callout, Cmd, Field, Loading, type Tone } from "../ui/primitives";

// ---------- types ----------

export type ParamKind = "installation" | "config" | "database" | "new_database" | "modules" | "text";
export type ParamSpec = { kind: ParamKind; description?: string; default?: string | string[] };
export type WorkflowRow = {
  name: string; source: "saved" | "built-in"; path: string | null; title: string | null; description: string | null;
  steps: number; params: string[]; error: string | null;
};
export type WorkflowDoc = {
  name: string; source: "saved" | "built-in"; path: string | null; text: string; digest: string; title: string | null;
  description: string | null; params: Record<string, ParamSpec>; derived: string[];
};
export type PreviewStep = {
  index: number; op: string; title: string; gate: boolean; ok: boolean; checks: Check[]; commands: string[];
  identity: string; skipped?: string;
};
export type Preview = {
  name: string; title: string; description: string | null; source: string; digest: string;
  params: Record<string, string | string[]>; start: number; steps: PreviewStep[]; gates: number;
};
export type RunStep = { index: number; op: string; title: string; status: string; summary?: string; identity?: string; commands?: string[]; seconds?: number };
export type RunRow = {
  run: string; workflow: string; title: string; at: string; ended_at: string | null; status: string; failed_at: number | null;
  start: number; params: Record<string, string | string[]>; retry_of: string | null; titles: string[]; steps: RunStep[]; user?: string;
};
export type OpInfo = { op: string; title: string; fields: Record<string, string>; required: string[]; gate: boolean; always_gate: boolean };

export const RUN_TONE: Record<string, Tone> = {
  ok: "ok", fail: "bad", declined: "warn", cancelled: "warn", interrupted: "warn", running: "info",
};
const LOG_LIMIT = 400_000;

// ---------- parameters ----------

/** One input per workflow parameter, with discovered installations, configs and databases to pick from. */
export function ParamsForm({ specs, values, onChange }: {
  specs: Record<string, ParamSpec>; values: Record<string, string>; onChange: (v: Record<string, string>) => void;
}) {
  const app = useApp();
  const snap = app.snap;
  const set = (k: string, v: string) => onChange({ ...values, [k]: v });
  const configKey = Object.keys(specs).find((k) => specs[k].kind === "config");
  const configPath = configKey ? values[configKey] : undefined;
  const installKey = Object.keys(specs).find((k) => specs[k].kind === "installation");
  const root = (installKey && values[installKey]) || snap?.instances.find((i) => i.path === configPath)?.installation || undefined;
  const dbNames = useMemo(() => (snap?.databases ?? []).filter((e) => !root || e.installation === root).flatMap((e) => e.databases.map((d) => d.name)), [snap, root]);
  const names = Object.keys(specs);
  if (!names.length) return <p className="muted small">This workflow takes no parameters.</p>;
  return (
    <div className="stack tight">
      <datalist id="task-dbs">{dbNames.map((d) => <option key={d} value={d} />)}</datalist>
      {names.map((k) => {
        const s = specs[k];
        const v = values[k] ?? "";
        const label = <span className="row tight"><span className="mono">{k}</span><Badge>{s.kind.replace("_", " ")}</Badge></span>;
        let input;
        if (s.kind === "installation") {
          input = (
            <select value={v} onChange={(e) => set(k, e.target.value)} aria-label={k}>
              <option value="">Choose an installation…</option>
              {snap?.installations.map((i) => <option key={i.root} value={i.root}>{i.root} (Odoo {i.version ?? "?"})</option>)}
            </select>
          );
        } else if (s.kind === "config") {
          input = (
            <select value={v} onChange={(e) => set(k, e.target.value)} aria-label={k}>
              <option value="">Choose a config…</option>
              {snap?.instances.filter((i) => i.installation).map((i) => <option key={i.path} value={i.path}>{i.name} — {i.path}</option>)}
            </select>
          );
        } else {
          const placeholder = s.kind === "modules" ? "sale_custom, hr_extra" : s.kind === "new_database" ? "name of the new database" : "";
          input = <input className="mono" value={v} aria-label={k} placeholder={placeholder} list={s.kind === "database" ? "task-dbs" : undefined}
            onChange={(e) => set(k, e.target.value)} />;
        }
        return <Field key={k} label={label} hint={s.description}>{input}</Field>;
      })}
    </div>
  );
}

export function initialValues(specs: Record<string, ParamSpec>, given?: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, s] of Object.entries(specs)) {
    const g = given?.[k] ?? s.default;
    out[k] = Array.isArray(g) ? g.join(", ") : g == null ? "" : String(g);
  }
  return out;
}

// ---------- preview step ----------

function StepCard({ s, state, summary }: { s: PreviewStep; state?: string; summary?: string }) {
  const icon = state === "ok" ? <CheckCircle2 className="ok-text" /> : state === "fail" ? <XCircle className="bad-text" />
    : state === "running" ? <span className="spinner" /> : state === "gate" ? <Pause className="warn-text" />
    : state && state !== "pending" ? <SkipForward className="dim" /> : <Circle className="dim" />;
  return (
    <li className={`task-step${state ? ` ${state}` : ""}`}>
      <div className="row tight" style={{ alignItems: "flex-start" }}>
        <span className="task-step-icon">{icon}</span>
        <div className="stack tight grow" style={{ minWidth: 0 }}>
          <div className="row tight wrap">
            <span className="strong">{s.index + 1}. {s.title}</span>
            <Badge mono>{s.op}</Badge>
            {s.gate && <Badge tone="warn" title="Waits for your confirmation before it runs">confirm</Badge>}
            {s.skipped && <Badge tone="ok">{s.skipped}</Badge>}
            {!s.skipped && !s.ok && !state && <Badge tone="bad">checks fail now</Badge>}
          </div>
          {summary && <span className={`xs ${state === "fail" ? "bad-text" : "dim"} break`}>{summary}</span>}
          {!s.skipped && (
            <Disclosure open={!state && (s.gate || !s.ok)} title={<span className="xs dim">as {s.identity || "?"} · {s.commands.length} command(s) · {s.checks.length} check(s)</span>}>
              <Checks checks={s.checks} />
              {s.commands.map((c) => <Cmd key={c}>{c}</Cmd>)}
            </Disclosure>
          )}
        </div>
      </div>
    </li>
  );
}

// ---------- run dialog ----------

type GateEvent = { run_id: string; task_step: number; step: string; status: string; text: string; plan?: { identity: string; commands: string[]; checks: Check[] } };
type Finished = { run_id: string; ok: boolean; error: string | null; status?: string; failed_at?: number | null; steps?: RunStep[] };

/** Preview a workflow with its parameters, run it, answer its gates, then retry from the failed step if needed. */
export function TaskRunDialog({ name, given, retryOf, onClose }: {
  name: string; given?: Record<string, unknown>; retryOf?: string; onClose: (ran: boolean) => void;
}) {
  const app = useApp();
  const [doc, setDoc] = useState<WorkflowDoc | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState<string | undefined>(retryOf);
  const [runId, setRunId] = useState<string | null>(null);
  const [states, setStates] = useState<Record<number, { state: string; summary?: string }>>({});
  const [gate, setGate] = useState<GateEvent | null>(null);
  const [log, setLog] = useState("");
  const [finished, setFinished] = useState<Finished | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const runRef = useRef<string | null>(null);

  useEffect(() => {
    rpc.request<WorkflowDoc>("tasks.read", { name }).then((d) => { setDoc(d); setValues(initialValues(d.params, given)); })
      .catch((e) => setError(String(e.message)));
  }, [name, given]);

  useEffect(() => {
    const offs = [
      rpc.on("tasks.step", (e: GateEvent) => {
        if (e.run_id !== runRef.current) return;
        if (e.step === "task") {
          const st = e.status === "start" ? "running" : e.status;
          setStates((old) => ({ ...old, [e.task_step]: { state: st, summary: e.status === "start" || e.status === "gate" ? undefined : e.text } }));
          if (e.status === "gate") setGate(e); else if (e.status !== "start") setGate(null);
          const line = e.status === "start" ? `\n== step ${e.task_step + 1}: ${e.text}` : e.status === "gate" ? `== step ${e.task_step + 1}: waiting for your confirmation`
            : `== step ${e.task_step + 1}: ${e.status}${e.text ? ` ${e.text}` : ""}`;
          setLog((old) => (old + line + "\n").slice(-LOG_LIMIT));
          return;
        }
        const line = e.status === "output" ? e.text : e.status === "start" ? `-- ${e.step}: ${e.text}` : `-- ${e.step}: ${e.status}${e.text ? ` ${e.text}` : ""}`;
        setLog((old) => (old + line + "\n").slice(-LOG_LIMIT));
      }),
      rpc.on("tasks.finished", (e: Finished) => { if (e.run_id === runRef.current) { setFinished(e); setGate(null); setCancelling(false); } }),
    ];
    return () => offs.forEach((off) => off());
  }, []);

  const target = () => (retry ? { retry_of: retry } : { name, params: values });
  const review = async () => {
    setError(null); setBusy(true);
    try { setPreview(await rpc.request<Preview>("tasks.preview", target())); } catch (e) { setError(String((e as Error).message)); } finally { setBusy(false); }
  };
  useEffect(() => { if (retryOf) review(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    setError(null);
    try {
      const r = await rpc.request<{ run_id: string; start: number }>("tasks.run", target());
      runRef.current = r.run_id;
      setRunId(r.run_id); setStates({}); setLog(""); setFinished(null); setGate(null);
    } catch (e) { setError(String((e as Error).message)); }
  };
  const answer = async (approve: boolean) => {
    if (!gate) return;
    try { await rpc.request("tasks.confirm", { run_id: gate.run_id, step: gate.task_step, approve }); setGate(null); }
    catch (e) { app.onError(String((e as Error).message)); }
  };
  const cancel = async () => {
    if (!runId) return;
    setCancelling(true);
    try { await rpc.request("tasks.cancel", { run_id: runId }); } catch (e) { app.onError(String((e as Error).message)); setCancelling(false); }
  };
  const retryNow = async () => {
    if (!finished) return;
    const failedRun = runId!;
    setRetry(failedRun); setRunId(null); runRef.current = null; setFinished(null); setPreview(null); setStates({}); setLog("");
    setBusy(true);
    try { setPreview(await rpc.request<Preview>("tasks.preview", { retry_of: failedRun })); } catch (e) { setError(String((e as Error).message)); } finally { setBusy(false); }
  };

  const running = !!runId && !finished;
  const allOk = preview?.steps.every((s) => s.ok) ?? false;
  const failedSteps = preview?.steps.filter((s, i) => i >= (preview?.start ?? 0) && !s.ok).length ?? 0;
  return (
    <Dialog size="xl" icon={<ListChecks />} title={preview?.title ?? doc?.title ?? name} subtitle={retry ? `Retry of run ${retry} from its failed step` : doc?.description ?? undefined}
      onClose={() => onClose(!!runId)} locked={running}
      footer={<>
        {running && <button className="btn" disabled={cancelling} onClick={cancel}><Square />{cancelling ? "Stopping after this step…" : "Stop after this step"}</button>}
        <span className="grow" />
        <button className="btn" disabled={running} onClick={() => onClose(!!runId)}>{runId ? "Close" : "Cancel"}</button>
        {!runId && !preview && <button className="btn primary" disabled={busy || !doc} onClick={review}>{busy ? "Planning…" : "Review steps"}</button>}
        {!runId && preview && !retry && <button className="btn" disabled={busy} onClick={() => setPreview(null)}>Change parameters</button>}
        {!runId && preview && <button className="btn primary" onClick={start}><Play />{preview.start ? `Run from step ${preview.start + 1}` : "Run"}</button>}
        {finished && finished.status !== "ok" && finished.failed_at != null && <button className="btn primary" onClick={retryNow}><RotateCcw />Retry from step {finished.failed_at + 1}…</button>}
      </>}>
      {error && <Callout tone="bad">{error}</Callout>}
      {!doc && !error && <Loading />}
      {doc && !preview && !runId && !retry && (
        <ParamsForm specs={doc.params} values={values} onChange={setValues} />
      )}
      {preview && (
        <div className="stack">
          {!runId && (
            <>
              <div className="row tight wrap">
                {Object.entries(preview.params).map(([k, v]) => <Badge key={k} mono>{k} = {Array.isArray(v) ? v.join(",") : v}</Badge>)}
              </div>
              <Callout tone={allOk ? "info" : "warn"}>
                {preview.start ? `${preview.steps.length - preview.start} of ${preview.steps.length} step(s) run` : `${preview.steps.length} step(s)`}, {preview.gates} wait for your confirmation. Each step is planned again just before it runs, and the first failure stops the run{failedSteps ? `. ${failedSteps} step(s) fail their checks now: a step may depend on one before it (a copy that does not exist yet), so read them before you start` : ""}.
              </Callout>
            </>
          )}
          {gate && (
            <Callout tone="warn" title={`Step ${gate.task_step + 1} waits for your confirmation`}
              action={<div className="row tight">
                <button className="btn sm" onClick={() => answer(false)}>Stop the run</button>
                <button className="btn sm primary" onClick={() => answer(true)}>Run this step</button>
              </div>}>
              <div className="stack tight">
                <span>{gate.text}, as <strong>{gate.plan?.identity}</strong></span>
                {gate.plan?.commands.map((c) => <Cmd key={c}>{c}</Cmd>)}
              </div>
            </Callout>
          )}
          {finished && (
            <Callout tone={finished.ok && finished.status === "ok" ? "ok" : "bad"}>
              {finished.error ? finished.error : finished.status === "ok" ? "Every step finished."
                : `Stopped at step ${(finished.failed_at ?? 0) + 1} (${finished.status}). Earlier steps are not undone; snapshots and receipts stay. Retry runs the same parameters from that step after planning it again.`}
            </Callout>
          )}
          <ol className="task-steps">
            {preview.steps.map((s) => <StepCard key={s.index} s={s} state={runId ? (s.skipped ? "skipped" : states[s.index]?.state ?? "pending") : undefined} summary={states[s.index]?.summary} />)}
          </ol>
          {runId && <div className="job-log"><LogConsole text={log} jobs empty="Waiting for the first step…" /></div>}
        </div>
      )}
    </Dialog>
  );
}

// ---------- editor ----------

const TEMPLATE = `name = "My workflow"
description = "What it does"

[params.config]
kind = "config"
description = "Odoo config of the instance"

[params.database]
kind = "database"

[[steps]]
op = "db.snapshot"
config = "{config}"
database = "{database}"
`;

/** Edit a saved workflow's TOML (or start one from a recipe), checked as you type; save writes the text as is. */
export function WorkflowEditorDialog({ name, copyOf, onClose }: { name?: string; copyOf?: string; onClose: (saved: boolean) => void }) {
  const [text, setText] = useState<string | null>(name || copyOf ? null : TEMPLATE);
  const [fileName, setFileName] = useState(name ?? (copyOf ? `${copyOf}-copy` : ""));
  const [check, setCheck] = useState<{ ok: boolean; error: string | null } | null>(null);
  const [ops, setOps] = useState<OpInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    const src = name ?? copyOf;
    if (src) rpc.request<WorkflowDoc>("tasks.read", { name: src }).then((d) => setText(d.text)).catch((e) => setError(String(e.message)));
    rpc.request<{ ops: OpInfo[] }>("tasks.ops").then((r) => setOps(r.ops)).catch(() => setOps([]));
  }, [name, copyOf]);
  useEffect(() => {
    if (text == null) return;
    const t = setTimeout(() => { rpc.request<{ ok: boolean; error: string | null }>("tasks.check", { text }).then(setCheck).catch(() => {}); }, 300);
    return () => clearTimeout(t);
  }, [text]);
  const save = async () => {
    setSaving(true); setError(null);
    try { await rpc.request("tasks.save", { name: fileName, text, overwrite: !!name }); onClose(true); }
    catch (e) { setError(String((e as Error).message)); } finally { setSaving(false); }
  };
  return (
    <Dialog size="xl" icon={<FileCode2 />} title={name ? `Edit ${name}` : copyOf ? `Copy of ${copyOf}` : "New workflow"} subtitle="TOML; saved in ~/.config/odoo-dev-panel/workflows" onClose={() => onClose(false)}
      footer={<>
        <button className="btn" onClick={() => onClose(false)}>Cancel</button>
        <button className="btn primary" disabled={saving || !check?.ok || !fileName} onClick={save}><Save />{saving ? "Saving…" : "Save"}</button>
      </>}>
      {error && <Callout tone="bad">{error}</Callout>}
      {!name && (
        <Field label="Name" hint="File name: letters, digits, _ . - ; built-in recipe names are taken">
          <input className="mono" value={fileName} onChange={(e) => setFileName(e.target.value)} autoFocus />
        </Field>
      )}
      {text == null ? <Loading /> : (
        <textarea className="config" style={{ minHeight: 360 }} value={text} spellCheck={false} aria-label="Workflow text" onChange={(e) => setText(e.target.value)} />
      )}
      {check && (check.ok ? <Callout tone="ok">The workflow is valid. Parameter values are checked when you run it.</Callout> : <Callout tone="bad">{check.error}</Callout>)}
      <Callout tone="info">
        Steps are typed operations. <code>{"{name}"}</code> takes a parameter; with one config parameter, <code>{"{installation}"}</code> is its installation.
        Steps that change data wait for a confirmation unless they say <code>auto = true</code>; <code>command</code> and <code>db.drop</code> always do.
        A command is an argument list (no shell), run as you or with <code>as = "run-as"</code> as the installation's run-as user.
      </Callout>
      {ops && ops.length > 0 && (
        <Disclosure title={`Step types (${ops.length})`}>
          <div className="list">
            {ops.map((o) => (
              <div key={o.op} className="list-row" style={{ alignItems: "flex-start" }}>
                <span className="mono small strong" style={{ minWidth: 140 }}>{o.op}</span>
                <span className="small grow">{o.title}<br /><span className="xs dim mono">{Object.entries(o.fields).map(([f, t]) => `${f}${o.required.includes(f) ? "*" : ""}: ${t}`).join(", ")}</span></span>
                {(o.gate || o.always_gate) && <Badge tone="warn">{o.always_gate ? "always confirmed" : "confirmed"}</Badge>}
              </div>
            ))}
          </div>
        </Disclosure>
      )}
    </Dialog>
  );
}
