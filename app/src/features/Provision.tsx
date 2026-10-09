import { FolderOpen, PackagePlus } from "lucide-react";
import { useState } from "react";
import { rpc } from "../rpc";
import { useApp } from "../state/app";
import type { Check, Step } from "../types";
import { Dialog } from "../ui/Dialog";
import { Checks, Disclosure, type Finished, JobView, Steps, useJob } from "../ui/Job";
import { Callout, Field, Segmented } from "../ui/primitives";

type Plan = { spec: Record<string, unknown>; preflight: Check[]; ok: boolean; steps: Step[]; root_script: string; config: string };
type Done = Finished & { root: string; run_as: string; conf_path: string };

const VERSIONS = [20, 19, 18, 17, 16, 15];

type Form = {
  version: number; run_as: string; config_name: string; http_port: string; odoo_branch: string;
  enterprise: "none" | "git" | "archive"; enterprise_git: string; enterprise_archive: string; custom: string;
};
const EMPTY: Form = {
  version: 17, run_as: "", config_name: "default", http_port: "", odoo_branch: "",
  enterprise: "none", enterprise_git: "", enterprise_archive: "", custom: "",
};

function toSpec(f: Form) {
  return {
    version: f.version,
    run_as: f.run_as.trim(),
    config_name: f.config_name.trim() || "default",
    http_port: f.http_port.trim() ? Number(f.http_port) : null,
    odoo_branch: f.odoo_branch.trim(),
    enterprise_git: f.enterprise === "git" ? f.enterprise_git.trim() : "",
    enterprise_archive: f.enterprise === "archive" ? f.enterprise_archive.trim() : "",
    custom: f.custom.split("\n").map((l) => l.trim()).filter(Boolean),
  };
}

/** Provision a new installation: form, review (nothing changed yet), run. */
export function ProvisionDialog({ onClose }: { onClose: () => void }) {
  const app = useApp();
  const [stage, setStage] = useState<"form" | "review" | "run">("form");
  const [form, setForm] = useState<Form>(EMPTY);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const job = useJob<Done>("provision");
  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((f) => ({ ...f, [key]: value }));
  // Native file chooser through the core (zenity or kdialog, as the developer); the path is still checked in review.
  const browseArchive = async () => {
    setError(null);
    try {
      const { path } = await rpc.request<{ path: string | null }>("desktop.pickFile",
        { title: `Odoo ${form.version} Enterprise archive`, kind: "archive", start: form.enterprise_archive.trim() });
      if (path) set("enterprise_archive", path);
    } catch (e) {
      setError(String((e as Error).message));
    }
  };

  const review = async () => {
    setBusy(true); setError(null);
    try {
      setPlan(await rpc.request<Plan>("provision.plan", toSpec(form)));
      setStage("review");
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };
  const start = async () => {
    setBusy(true); setError(null);
    try {
      const r = await rpc.request<{ run_id: string }>("provision.run", toSpec(form));
      job.begin(r.run_id);
      setStage("run");
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };

  const close = () => { if (job.finished) { app.scan(); app.refresh(); } onClose(); };
  const reuse = !!plan?.preflight.some((c) => ["root-path", "run-as-user", "config-file"].includes(c.id) && c.status === "warn");
  const spec = plan?.spec as { run_as?: string; root?: string; conf_path?: string } | undefined;
  const f = job.finished;

  return (
    <Dialog size="lg" icon={<PackagePlus />} title="New Odoo installation" onClose={close} locked={job.busy}
      subtitle={stage === "form" ? "Step 1 of 3 · Choose" : stage === "review" ? "Step 2 of 3 · Review: nothing has changed yet" : "Step 3 of 3 · Install"}
      footer={stage === "form" ? (
        <><button className="btn" onClick={close}>Cancel</button><button className="btn primary" disabled={busy} onClick={review}>{busy ? "Checking…" : "Review"}</button></>
      ) : stage === "review" && plan ? (
        <>
          <button className="btn" onClick={() => setStage("form")}>Back</button>
          <button className="btn primary" disabled={busy || !plan.ok} onClick={start}>
            {plan.ok ? (reuse ? "Reuse and repair installation" : "Create installation") : "Fix the failed checks first"}
          </button>
        </>
      ) : <button className="btn" disabled={job.busy} onClick={close}>Close</button>}>
      {error && <Callout tone="bad">{error}</Callout>}

      {stage === "form" && (
        <>
          <Field label="Odoo version">
            <Segmented label="Odoo version" value={String(form.version)} onChange={(v) => set("version", Number(v))}
              options={VERSIONS.map((v) => ({ value: String(v), label: String(v) }))} />
          </Field>
          <div className="grid2">
            <Field label="Linux user" hint={`default odoo${form.version}`}>
              <input value={form.run_as} placeholder={`odoo${form.version}`} onChange={(e) => set("run_as", e.target.value)} />
            </Field>
            <Field label="First config name">
              <input value={form.config_name} onChange={(e) => set("config_name", e.target.value)} />
            </Field>
            <Field label="HTTP port" hint="optional">
              <input value={form.http_port} inputMode="numeric" className="mono" onChange={(e) => set("http_port", e.target.value.replace(/\D/g, ""))} />
            </Field>
            <Field label="Community branch" hint={`default ${form.version}.0`}>
              <input value={form.odoo_branch} className="mono" placeholder={`${form.version}.0`} onChange={(e) => set("odoo_branch", e.target.value)} />
            </Field>
          </div>
          <Field label="Enterprise source">
            <Segmented label="Enterprise source" value={form.enterprise} onChange={(v) => set("enterprise", v)}
              options={[{ value: "none", label: "None (community only)" }, { value: "git", label: "Git URL" }, { value: "archive", label: "Local archive" }]} />
          </Field>
          {form.enterprise === "git" && (
            <Field label="Enterprise git URL" hint="this machine needs access">
              <input className="mono" value={form.enterprise_git} placeholder="git@github.com:odoo/enterprise.git" onChange={(e) => set("enterprise_git", e.target.value)} />
            </Field>
          )}
          {form.enterprise === "archive" && (
            <Field label="Enterprise archive path" hint=".zip or .tar.*; checked during review">
              <div className="row tight">
                <input className="mono grow" value={form.enterprise_archive} placeholder="/home/you/enterprise-17.0.zip" onChange={(e) => set("enterprise_archive", e.target.value)} />
                <button type="button" className="btn" onClick={browseArchive}><FolderOpen />Browse…</button>
              </div>
            </Field>
          )}
          <Field label="Custom addon repositories" hint="one per line: [NAME=]URL[#BRANCH]">
            <textarea className="mono" rows={3} value={form.custom} onChange={(e) => set("custom", e.target.value)} />
          </Field>
        </>
      )}

      {stage === "review" && plan && (
        <>
          <Callout tone="info">
            Creates or reuses user <b>{spec?.run_as}</b> and <code>{spec?.root}</code>. Existing files, configs and source trees are kept; a broken venv is
            moved aside and rebuilt. After you start, sudo asks for your password once.
          </Callout>
          <Checks checks={plan.preflight} />
          <Steps steps={plan.steps} actors />
          <Disclosure title="Script that runs as root (read before you start)"><pre className="block">{plan.root_script}</pre></Disclosure>
          <Disclosure title="Config that will be written"><pre className="block">{plan.config}</pre></Disclosure>
        </>
      )}

      {stage === "run" && (
        <JobView job={job}
          done={f && <>Odoo {form.version} is ready in <code>{f.root}</code>.</>}
          failed="Failed. Nothing is rolled back.">
          {f?.ok && <Callout tone="ok">Config: <code>{f.conf_path}</code>. Unlock <b>{f.run_as}</b> (Sessions → Agents) to start it.</Callout>}
          {f && !f.ok && (
            <Callout tone="warn">
              Run it again: finished parts are reused and broken ones are repaired. If a receipt exists, <code>{f.root}/.odp-provision.json</code> shows the last finished phase.
            </Callout>
          )}
        </JobView>
      )}
    </Dialog>
  );
}
