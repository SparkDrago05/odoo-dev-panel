import {
  AlertTriangle, CheckCircle2, ClipboardCopy, Cpu, Download, FileText, FlaskConical, GitCompare, HardDrive, PackagePlus, Plus, RefreshCw, Wrench, X, XCircle,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { rpc } from "../rpc";
import { useApp } from "../state/app";
import type { Check, PyDisk, PyEnv, PyPlan, PyReqFile, PyTool, Step } from "../types";
import { Dialog } from "../ui/Dialog";
import { Checks, Disclosure, type Finished, JobView, Steps, useJob } from "../ui/Job";
import { Badge, Callout, EmptyState, Field, KV, Loading, Segmented, type Tone } from "../ui/primitives";
import { Panel } from "../views/common";

const STATUS_TONE: Record<string, Tone> = { ok: "ok", missing: "bad", mismatch: "warn", "not-applicable": "idle", unknown: "info", "manifest-missing": "warn" };
const mib = (b: number) => (b > 1024 ** 3 ? `${(b / 1024 ** 3).toFixed(1)} GiB` : `${Math.round(b / 1024 ** 2)} MiB`);

/** The Python tab of an installation: interpreter, requirement status, conflicts, packages, actions, dev tools. */
export function PythonPanel({ root }: { root: string }) {
  const app = useApp();
  const [data, setData] = useState<PyEnv | null>(null);
  const [tools, setTools] = useState<PyTool[] | null>(null);
  const [disk, setDisk] = useState<PyDisk | null>(null);
  const [diskBusy, setDiskBusy] = useState(false);
  const [view, setView] = useState<"problems" | "all">("problems");
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await rpc.request<PyEnv>("python.env", { root }));
      setTools((await rpc.request<{ tools: PyTool[] }>("python.tools", { root })).tools);
    } catch (e) {
      setError(String((e as Error).message));
    }
  }, [root]);
  useEffect(() => { load(); }, [load, app.versions.python]);
  useEffect(() => rpc.on("python.finished", () => { load(); }), [load]);

  const rows = useMemo(() => (data?.requirements ?? []).filter((r) => view === "all" || ["missing", "mismatch", "unknown", "manifest-missing"].includes(r.status)), [data, view]);
  const rel = (f: string) => (f.startsWith(root + "/") ? f.slice(root.length + 1) : f);
  const act = (params: Record<string, unknown>) => app.setDialog({ kind: "python-action", root, params });
  const reqFile = async (op: "add" | "remove", path: string) => {
    try {
      await rpc.request("python.reqfile", { root, op, path });
      app.notify("ok", op === "add" ? "Added. Doctor and Install missing now include it." : "Removed from this installation.");
      await load();
    } catch (e) { app.onError(String((e as Error).message)); }
  };
  const loadDisk = async () => { setDiskBusy(true); try { setDisk(await rpc.request<PyDisk>("python.disk", { root })); } catch (e) { app.onError(String((e as Error).message)); } finally { setDiskBusy(false); } };
  const exportList = async () => {
    const out = await rpc.request<{ text: string }>("python.export", { root }).catch((e) => { app.onError(String(e.message)); return null; });
    if (out) { await navigator.clipboard.writeText(out.text); app.notify("ok", `Copied ${out.text.split("\n").length - 3} packages (name==version)`); }
  };

  if (error) return <Callout tone="bad">{error}</Callout>;
  if (!data) return <Loading>Reading the venv and requirement files…</Loading>;
  const it = data.interpreter;
  const problems = data.counts.missing + data.counts.mismatch;
  return (
    <>
      {it.problem && (
        <Callout tone="bad" title="The venv cannot run Odoo" action={<button className="btn sm primary" onClick={() => app.setDialog({ kind: "repair-venv", root })}><Wrench />Rebuild venv…</button>}>
          {it.problem}
        </Callout>
      )}
      <div className="grid3">
        <div className="panel stack tight" style={{ padding: 12 }}>
          <span className="section-title"><Cpu style={{ width: 12, height: 12 }} /> Interpreter</span>
          <KV items={[
            ["Runs", it.version ? `Python ${it.version}` : "—"],
            ["Pinned", it.pinned ? <span className="row tight">{it.pinned}{it.matches_pin === false && <Badge tone="warn">differs</Badge>}</span> : "—"],
            ["Built for", it.built_for],
            ["Kind", it.uv_managed ? "uv-managed" : it.system_python ? <Badge tone="warn">system Python</Badge> : "other"],
          ]} />
          <code className="xs dim break">{it.target ?? it.python ?? "no venv"}</code>
        </div>
        <div className="panel stack tight" style={{ padding: 12 }}>
          <span className="section-title"><PackagePlus style={{ width: 12, height: 12 }} /> Requirements</span>
          <KV items={[
            ["Packages", Object.keys(data.packages).length],
            ["Files", data.files.length],
            ["OK", data.counts.ok],
            ["Missing / mismatch", <span className={problems ? "bad-text" : ""}>{data.counts.missing} / {data.counts.mismatch}</span>],
            ["Conflicts", data.conflicts.length],
          ]} />
        </div>
        <div className="panel stack tight" style={{ padding: 12 }}>
          <span className="section-title"><HardDrive style={{ width: 12, height: 12 }} /> Disk</span>
          {disk ? (
            <KV items={[...disk.parts.map((p) => [p.label, `${mib(p.bytes)}${p.complete ? "" : "+"}`] as [string, string]), ["Free", `${mib(disk.free)} of ${mib(disk.total)}`]]} />
          ) : <button className="btn sm" disabled={diskBusy} onClick={loadDisk}><HardDrive />{diskBusy ? "Measuring…" : "Measure"}</button>}
        </div>
      </div>

      <div className="row wrap" style={{ gap: 8 }}>
        <button className="btn sm primary" disabled={!problems} onClick={() => act({ op: "install", missing: true })}><PackagePlus />Install missing ({problems})</button>
        <button className="btn sm" onClick={() => act({ op: "validate" })}><FlaskConical />Validate imports…</button>
        <button className="btn sm" onClick={exportList}><ClipboardCopy />Copy package list</button>
        <button className="btn sm" onClick={() => app.setDialog({ kind: "compare", mode: "installations", path: app.snap?.instances.find((i) => i.installation === root)?.path })}><GitCompare />Compare…</button>
        <button className="btn sm" onClick={() => app.setDialog({ kind: "repair-venv", root })}><Wrench />Rebuild venv…</button>
        <button className="btn ghost sm" onClick={load}><RefreshCw />Refresh</button>
      </div>

      {data.conflicts.map((c) => (
        <Callout key={c.name} tone="bad" title={`Conflicting requirements for ${c.name}`}>
          {c.reason}
          {c.asked.map((a) => <div key={a.file} className="mono xs">{a.spec} <span className="dim">in {rel(a.file)}</span></div>)}
        </Callout>
      ))}

      <Panel flush title={<div className="row"><h2>Requirements</h2><span className="xs dim">from {data.files.length} file(s); measured from dist-info, not guessed</span></div>}
        actions={<Segmented label="Show" value={view} onChange={setView} options={[{ value: "problems", label: `Problems (${data.counts.missing + data.counts.mismatch + data.counts.unknown + data.counts["manifest-missing"]})` }, { value: "all", label: `All (${data.requirements.length})` }]} />}>
        {rows.length === 0 ? <EmptyState icon={<CheckCircle2 />} title="Every requirement is met">{data.counts["not-applicable"]} line(s) do not apply to this Python.</EmptyState> : (
          <div className="list">
            {rows.map((r, n) => (
              <div key={n} className="list-row" style={{ minHeight: 38 }}>
                <Badge tone={STATUS_TONE[r.status]}>{r.status === "manifest-missing" ? "addon needs" : r.status}</Badge>
                <span className="strong mono small" style={{ minWidth: 160 }}>{r.name}</span>
                <span className="mono xs" style={{ minWidth: 110 }}>{r.spec || "any"}</span>
                <span className="mono xs dim" style={{ minWidth: 90 }}>{r.installed ?? "—"}{r.installed_as && r.installed_as !== r.name ? ` (${r.installed_as})` : ""}</span>
                <span className="xs dim truncate grow" title={r.detail}>{rel(r.file)}</span>
                {(r.status === "missing" || r.status === "mismatch" || r.status === "manifest-missing") && (
                  <button className="btn ghost sm" onClick={() => act({ op: "install", packages: [`${r.name}${r.spec.replace(/\s+/g, "")}`] })}><Download />Install</button>
                )}
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel flush title={<div className="row"><h2>Requirement files</h2><span className="xs dim">{data.detected.length} found under {root}</span></div>}>
        {data.detected.length === 0 ? <EmptyState icon={<FileText />} title="No requirements.txt found">Nothing named requirements*.txt exists under this installation.</EmptyState> : (
          <div className="list">
            {data.detected.map((f: PyReqFile) => (
              <div key={f.path} className="list-row" style={{ minHeight: 38 }}>
                <Badge tone={f.state === "available" ? "idle" : "ok"}>{f.state === "available" ? "not used" : f.state}</Badge>
                <span className="mono xs truncate grow" title={f.path}>{rel(f.path)}</span>
                <span className="xs dim">{f.count} line(s)</span>
                {f.state === "available" && <button className="btn ghost sm" onClick={() => reqFile("add", f.path)}><Plus />Add</button>}
                {f.state === "added" && <button className="btn ghost sm" onClick={() => reqFile("remove", f.path)}><X />Remove</button>}
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel title="Install packages">
        <Field label="Package specifiers" hint="Space or comma separated: name, name==1.2, name[extra]>=2. Installed with uv into this venv as its run-as user. URLs, paths and pip options are refused.">
          <div className="row tight">
            <input className="mono grow" value={typed} placeholder="openupgradelib phonenumbers==8.13.*" onChange={(e) => setTyped(e.target.value)} />
            <button className="btn" disabled={!typed.trim()} onClick={() => act({ op: "install", packages: typed.split(/[\s,]+(?![^[]*\])/).filter(Boolean) })}><PackagePlus />Review…</button>
          </div>
        </Field>
      </Panel>

      <Panel title="Development tools">
        {!tools ? <Loading /> : (
          <div className="list">
            {tools.map((t) => (
              <div key={t.tool} className="list-row">
                {t.installed && (t.tool !== "wkhtmltopdf" || t.patched) ? <CheckCircle2 style={{ width: 15, color: "var(--success)" }} /> : t.installed ? <AlertTriangle style={{ width: 15, color: "var(--warning)" }} /> : <XCircle style={{ width: 15, color: "var(--text-3)" }} />}
                <span className="strong" style={{ minWidth: 110 }}>{t.tool}</span>
                <Badge>{t.scope === "venv" ? "this venv" : "system, sudo"}</Badge>
                <span className="xs dim truncate grow">{t.version ?? t.detail}</span>
                {(!t.installed || (t.tool === "wkhtmltopdf" && !t.patched)) && (
                  <button className="btn sm" onClick={() => act({ op: "tool", tool: t.tool })}><Download />Install…</button>
                )}
              </div>
            ))}
          </div>
        )}
      </Panel>

      {data.extras.length > 0 && (
        <Disclosure title={`${data.extras.length} installed package(s) no requirement file asks for`}>
          <div className="row tight wrap">{data.extras.map((e) => <Badge key={e} mono>{e} {data.packages[e]}</Badge>)}</div>
        </Disclosure>
      )}
    </>
  );
}

type Result = Finished & {
  kind: string; ok?: boolean; exit_code?: number | null; packages?: string[]; failed?: string[]; tool?: string;
  validation?: { python: string; odoo: { ok: boolean; version?: string; error?: string } | null; modules: Record<string, { ok: boolean; error: string | null }> } | null;
};

/** Install packages, validate imports or install a dev tool: plan (with the root script for system tools), run, result. */
export function PythonActionDialog({ root, params, onClose }: { root: string; params: Record<string, unknown>; onClose: (changed: boolean) => void }) {
  const [plan, setPlan] = useState<PyPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const job = useJob<Result>("python");
  const full = useMemo(() => ({ root, ...params }), [root, params]);
  useEffect(() => { rpc.request<PyPlan>("python.plan", full).then(setPlan).catch((e) => setError(String(e.message))); }, [full]);
  const start = async () => {
    setError(null);
    try { job.begin((await rpc.request<{ run_id: string }>("python.run", full)).run_id); } catch (e) { setError(String((e as Error).message)); }
  };
  const f = job.finished;
  const op = params.op as string;
  const title = op === "validate" ? "Validate imports" : op === "tool" ? `Install ${params.tool}` : "Install packages";
  const system = op === "tool" && params.tool !== "debugpy";
  return (
    <Dialog size="lg" icon={op === "validate" ? <FlaskConical /> : <PackagePlus />} title={title} subtitle={root} onClose={() => onClose(!!f)} locked={job.busy}
      footer={<>
        <button className="btn" disabled={job.busy} onClick={() => onClose(!!f)}>{job.started ? "Close" : "Cancel"}</button>
        {!job.started && plan && <button className="btn primary" disabled={!plan.ok} onClick={start}>{plan.ok ? (op === "validate" ? "Run check" : system ? "Install (asks for sudo)" : "Install") : "Fix the failed checks first"}</button>}
      </>}>
      {error && <Callout tone="bad">{error}</Callout>}
      {!job.started && !plan && !error && <Loading />}
      {!job.started && plan && (
        <>
          {op === "install" && <Callout tone="info">Runs <code>uv pip install</code> with this venv's interpreter, as {plan.steps[0]?.actor}, through its agent. Nothing is uninstalled.</Callout>}
          {op === "validate" && <Callout tone="info">Imports odoo and the top-level modules of each required package with this venv's Python, as the run-as user. Nothing changes.</Callout>}
          {system && <Callout tone="warn">A system package: one root script, run with sudo. Read it below before you start.{params.tool === "wkhtmltopdf" && " The file is checked against a pinned SHA256 before apt sees it."}</Callout>}
          {plan.packages && op === "install" && <div className="row tight wrap">{plan.packages.map((p) => <Badge key={p} mono>{p}</Badge>)}</div>}
          <Checks checks={plan.checks as Check[]} />
          <Steps steps={plan.steps as Step[]} actors />
          {plan.script && <Disclosure title="Script that runs as root" open><pre className="block">{plan.script}</pre></Disclosure>}
        </>
      )}
      {job.started && (
        <JobView job={job} done={f?.ok ? "Done." : "Finished with problems."}>
          {f?.validation && (
            <Callout tone={f.ok ? "ok" : "bad"} title={`odoo ${f.validation.odoo?.ok ? `imports (${f.validation.odoo.version ?? "?"})` : "does not import"} · ${Object.values(f.validation.modules).filter((m) => m.ok).length} of ${Object.keys(f.validation.modules).length} packages import`}>
              {f.validation.odoo && !f.validation.odoo.ok && <div className="mono xs">{f.validation.odoo.error}</div>}
              {(f.failed ?? []).map((n) => <div key={n} className="mono xs">{n}: {f.validation!.modules[n].error}</div>)}
            </Callout>
          )}
          {f && f.kind !== "validate" && <Callout tone={f.ok ? "ok" : "bad"}>{f.ok ? "Installed. Restart Odoo processes of this installation to load the change." : `Failed (exit code ${f.exit_code}). The log above has the reason.`}</Callout>}
        </JobView>
      )}
    </Dialog>
  );
}
