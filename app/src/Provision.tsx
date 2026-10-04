import { useEffect, useRef, useState } from "react";
import { rpc } from "./rpc";

type Check = { id: string; status: "ok" | "warn" | "fail"; detail: string };
type Step = { id: string; phase: number; actor: string; title: string; commands: string[] };
type Plan = { spec: Record<string, unknown>; preflight: Check[]; ok: boolean; steps: Step[]; root_script: string; config: string };
type StepEvent = { run_id: string; step: string; status: "start" | "output" | "ok" | "fail"; text: string };
type Finished = { run_id: string; ok: boolean; error: string | null; root: string; run_as: string; conf_path: string };

const VERSIONS = [19, 18, 17, 16, 15];
const LOG_LIMIT = 400_000;

type Form = {
  version: number;
  run_as: string;
  config_name: string;
  http_port: string;
  odoo_branch: string;
  enterprise: "none" | "git" | "archive";
  enterprise_git: string;
  enterprise_archive: string;
  custom: string;
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

export default function Provision({ onClose }: { onClose: () => void }) {
  const [stage, setStage] = useState<"form" | "review" | "run">("form");
  const [form, setForm] = useState<Form>(EMPTY);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState("");
  const [current, setCurrent] = useState<string | null>(null);
  const [finished, setFinished] = useState<Finished | null>(null);
  const runId = useRef<string | null>(null);
  const logRef = useRef<HTMLPreElement>(null);
  const stick = useRef(true); // follow the log until the user scrolls up
  // Only user input changes it: programmatic scrolling must not look like "the user scrolled up".
  const checkStick = (el: HTMLElement) =>
    requestAnimationFrame(() => { stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40; });
  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((f) => ({ ...f, [key]: value }));

  useEffect(() => {
    const offs = [
      rpc.on("provision.step", (e: StepEvent) => {
        if (e.run_id !== runId.current) return;
        if (e.status === "start") {
          setCurrent(e.step);
          setLog((old) => `${old}\n== ${e.step}: ${e.text}\n`);
        } else if (e.status === "output") {
          setLog((old) => (old + e.text + "\n").slice(-LOG_LIMIT));
        } else {
          setLog((old) => `${old}== ${e.step}: ${e.status === "ok" ? "ok" : `FAILED ${e.text}`}\n`);
        }
      }),
      rpc.on("provision.finished", (e: Finished) => {
        if (e.run_id === runId.current) setFinished(e);
      }),
    ];
    return () => offs.forEach((off) => off());
  }, []);

  useEffect(() => {
    const el = logRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [log]);

  const review = async () => {
    setBusy(true);
    setError(null);
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
    setBusy(true);
    setError(null);
    try {
      setLog("");
      setFinished(null);
      const started = await rpc.request<{ run_id: string }>("provision.run", toSpec(form));
      runId.current = started.run_id;
      setStage("run");
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };

  const running = stage === "run" && !finished;
  const reuse = !!plan?.preflight.some((c) => ["root-path", "run-as-user", "config-file"].includes(c.id) && c.status === "warn");
  const spec = plan?.spec as { run_as?: string; root?: string; conf_path?: string } | undefined;

  return (
    <div className="modal-backdrop">
      <div className="modal wide">
        <div className="row between">
          <h2>New Odoo installation</h2>
          <button disabled={running} onClick={onClose}>Close</button>
        </div>
        {error && <div className="error" role="alert">{error}</div>}

        {stage === "form" && (
          <>
            <div className="grid2">
              <label>Odoo version
                <select value={form.version} onChange={(e) => set("version", Number(e.target.value))}>
                  {VERSIONS.map((v) => <option key={v}>{v}</option>)}
                </select>
              </label>
              <label>Linux user (default odoo{form.version})
                <input value={form.run_as} placeholder={`odoo${form.version}`} onChange={(e) => set("run_as", e.target.value)} />
              </label>
              <label>First config name
                <input value={form.config_name} onChange={(e) => set("config_name", e.target.value)} />
              </label>
              <label>HTTP port (optional)
                <input value={form.http_port} inputMode="numeric" onChange={(e) => set("http_port", e.target.value.replace(/\D/g, ""))} />
              </label>
              <label>Community branch (default {form.version}.0)
                <input value={form.odoo_branch} placeholder={`${form.version}.0`} onChange={(e) => set("odoo_branch", e.target.value)} />
              </label>
              <label>Enterprise source
                <select value={form.enterprise} onChange={(e) => set("enterprise", e.target.value as Form["enterprise"])}>
                  <option value="none">None (community only)</option>
                  <option value="git">Git URL</option>
                  <option value="archive">Local archive</option>
                </select>
              </label>
            </div>
            {form.enterprise === "git" && (
              <label>Enterprise git URL (this machine needs access)
                <input value={form.enterprise_git} placeholder="git@github.com:odoo/enterprise.git" onChange={(e) => set("enterprise_git", e.target.value)} />
              </label>
            )}
            {form.enterprise === "archive" && (
              <label>Enterprise archive path (.zip or .tar.*)
                <input value={form.enterprise_archive} placeholder="/home/you/enterprise-17.0.zip" onChange={(e) => set("enterprise_archive", e.target.value)} />
              </label>
            )}
            <label>Custom addon repositories, one per line: [NAME=]URL[#BRANCH]
              <textarea rows={3} value={form.custom} onChange={(e) => set("custom", e.target.value)} />
            </label>
            <div className="row end">
              <button className="primary" disabled={busy} onClick={review}>{busy ? "Checking…" : "Review"}</button>
            </div>
          </>
        )}

        {stage === "review" && plan && (
          <>
            <p className="muted">
              Nothing has changed yet. Creates or reuses user <b>{spec?.run_as}</b> and <b>{spec?.root}</b>. Existing files, configs and source trees are kept; a broken venv is moved aside and rebuilt.
              After you start, sudo asks for your password once.
            </p>
            <h3>Checks</h3>
            <table><tbody>
              {plan.preflight.map((c) => (
                <tr key={c.id}><td><span className={`dot ${c.status === "ok" ? "running" : c.status === "warn" ? "stopping" : "error"}`} /> {c.status}</td>
                  <td>{c.id}</td><td className="muted">{c.detail}</td></tr>
              ))}
            </tbody></table>
            <h3>Steps</h3>
            <ol>{plan.steps.map((s) => <li key={s.id}>[{s.actor}] {s.title}</li>)}</ol>
            <details><summary>Script that runs as root (read before you start)</summary><pre className="script">{plan.root_script}</pre></details>
            <details><summary>Config that will be written</summary><pre className="script">{plan.config}</pre></details>
            <div className="row end">
              <button onClick={() => setStage("form")}>Back</button>
              <button className="primary" disabled={busy || !plan.ok} onClick={start}>
                {plan.ok ? (reuse ? "Reuse and repair installation" : "Create installation") : "Fix the failed checks first"}
              </button>
            </div>
          </>
        )}

        {stage === "run" && (
          <>
            <p className="muted">
              {finished ? (finished.ok ? "Done." : "Failed.") : `Running${current ? `: ${current}` : ""}…`}
            </p>
            <pre
              ref={logRef}
              className="script tall"
              onWheel={(e) => checkStick(e.currentTarget)}
              onMouseUp={(e) => checkStick(e.currentTarget)}
            >{log}</pre>
            {finished && finished.ok && (
              <p>Odoo {form.version} is ready in <b>{finished.root}</b>. Config: <b>{finished.conf_path}</b>. Unlock <b>{finished.run_as}</b> in the main window to start it.</p>
            )}
            {finished && !finished.ok && (
              <p className="muted">
                Nothing is rolled back. Run it again: finished parts are reused and broken ones are repaired. If a receipt exists, {finished.root}/.odp-provision.json shows the last finished phase.
              </p>
            )}
            <div className="row end"><button disabled={running} onClick={onClose}>Close</button></div>
          </>
        )}
      </div>
    </div>
  );
}
