import { Fragment, useEffect, useRef, useState } from "react";
import { rpc } from "./rpc";

type Finding = {
  check: string; code: string; severity: "error" | "warning" | "info"; subject: string; title: string;
  detail: string; why: string; commands: string[]; repair: string | null; installation: string | null;
};
type Report = { findings: Finding[]; counts: Record<string, number>; not_checked: string[] };
type Check = { id: string; status: "ok" | "warn" | "fail"; detail: string };
type Step = { id: string; phase: number; actor: string; title: string; commands: string[] };
type Plan = {
  root: string; version: string; run_as: string; python: string; venv: string; requirements: string[];
  extras: string[]; carry_extras: boolean; checks: Check[]; steps: Step[]; ok: boolean;
};
type StepEvent = { run_id: string; step: string; status: "start" | "output" | "ok" | "fail"; text: string };
type Finished = { run_id: string; ok: boolean; error: string | null; venv: string; backup?: string | null };

const LOG_LIMIT = 400_000;
const DOT = { error: "error", warning: "stopping", info: "" } as const;

export default function Doctor({ onError }: { onError: (message: string) => void }) {
  const [report, setReport] = useState<Report | null>(null);
  const [running, setRunning] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const [repairRoot, setRepairRoot] = useState<string | null>(null);

  const run = async () => {
    setRunning(true);
    try {
      setReport(await rpc.request<Report>("doctor.run"));
    } catch (e) {
      onError(String((e as Error).message));
    } finally {
      setRunning(false);
    }
  };

  return (
    <section>
      <h2>Doctor</h2>
      <div className="row">
        <button onClick={run} disabled={running}>{running ? "Checking…" : report ? "Check again" : "Run checks"}</button>
        {report && (
          <span className="muted">
            {report.counts.error} errors, {report.counts.warning} warnings · read-only: nothing is changed unless you press Repair
          </span>
        )}
      </div>
      {report && report.findings.length === 0 && <p className="muted">No problems found.</p>}
      {report && report.findings.length > 0 && (
        <table>
          <tbody>
            {report.findings.map((f, i) => (
              <Fragment key={`${f.code}:${f.subject}`}>
                <tr onClick={() => setOpen(open === i ? null : i)}>
                  <td><span className={`dot ${DOT[f.severity]}`} /> {f.severity}</td>
                  <td className="muted">{f.check}</td>
                  <td>{f.title}{f.detail && <div className="muted">{f.detail}</div>}</td>
                  <td className="actions">
                    {f.repair === "venv" && f.installation && (
                      <button className="primary" onClick={(e) => { e.stopPropagation(); setRepairRoot(f.installation); }}>
                        Repair
                      </button>
                    )}
                  </td>
                </tr>
                {open === i && (
                  <tr>
                    <td colSpan={4}>
                      <p>{f.why}</p>
                      {f.commands.length > 0 && <p className="muted">Suggested commands (not run by the app):</p>}
                      {f.commands.map((c) => <code key={c} className="command">{c}</code>)}
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
      {report && report.not_checked.length > 0 && <p className="muted">Not checked: {report.not_checked.join("; ")}</p>}
      {repairRoot && <RepairVenv root={repairRoot} onClose={(changed) => { setRepairRoot(null); if (changed) run(); }} />}
    </section>
  );
}

function RepairVenv({ root, onClose }: { root: string; onClose: (changed: boolean) => void }) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [custom, setCustom] = useState(true);
  const [carry, setCarry] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [log, setLog] = useState("");
  const [started, setStarted] = useState(false);
  const [finished, setFinished] = useState<Finished | null>(null);
  const runId = useRef<string | null>(null);
  const logRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    setError(null);
    rpc.request<Plan>("repair.plan", { root, custom, carry_extras: carry })
      .then(setPlan)
      .catch((e) => setError(String(e.message)));
  }, [root, custom, carry]);

  useEffect(() => {
    const offs = [
      rpc.on("repair.step", (e: StepEvent) => {
        if (e.run_id !== runId.current) return;
        const line = e.status === "start" ? `\n== ${e.step}: ${e.text}` : e.status === "output" ? e.text
          : `== ${e.step}: ${e.status === "ok" ? "ok" : `FAILED ${e.text}`}`;
        setLog((old) => (old + line + "\n").slice(-LOG_LIMIT));
      }),
      rpc.on("repair.finished", (e: Finished) => { if (e.run_id === runId.current) setFinished(e); }),
    ];
    return () => offs.forEach((off) => off());
  }, []);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [log]);

  const start = async () => {
    setError(null);
    try {
      const r = await rpc.request<{ run_id: string }>("repair.run", { root, custom, carry_extras: carry });
      runId.current = r.run_id;
      setStarted(true);
    } catch (e) {
      setError(String((e as Error).message));
    }
  };

  const busy = started && !finished;
  return (
    <div className="modal-backdrop">
      <div className="modal wide">
        <div className="row between">
          <h2>Rebuild the venv of {root}</h2>
          <button disabled={busy} onClick={() => onClose(!!finished)}>Close</button>
        </div>
        {error && <div className="error" role="alert">{error}</div>}
        {!started && !plan && !error && <p className="muted">Checking…</p>}
        {!started && plan && (
          <>
            <p className="muted">
              Builds a new venv with Python {plan.python} next to the old one, as {plan.run_as}, and validates it.
              Only then the old venv is renamed to venv.bak-&lt;time&gt; and the new one takes its place. If anything
              fails before that, the old venv was never touched. Nothing is deleted.
            </p>
            <h3>Checks</h3>
            <table><tbody>
              {plan.checks.map((c) => (
                <tr key={c.id}>
                  <td><span className={`dot ${c.status === "ok" ? "running" : c.status === "warn" ? "stopping" : "error"}`} /> {c.status}</td>
                  <td>{c.id}</td><td className="muted">{c.detail}</td>
                </tr>
              ))}
            </tbody></table>
            <label><input type="checkbox" checked={custom} onChange={(e) => setCustom(e.target.checked)} /> Install the requirements.txt files of custom repositories</label>
            <p className="muted">Requirement files: {plan.requirements.join(", ") || "none"}</p>
            {plan.extras.length > 0 && (
              <>
                <label>
                  <input type="checkbox" checked={carry} onChange={(e) => setCarry(e.target.checked)} /> Also install the {plan.extras.length} package(s)
                  of the old venv that no requirements file names (latest compatible versions)
                </label>
                <p className="muted">{plan.extras.join(" ")}</p>
              </>
            )}
            <h3>Steps</h3>
            <ol>
              {plan.steps.map((s) => (
                <li key={s.id}>{s.title}{s.commands.map((c) => <code key={c} className="command">{c}</code>)}</li>
              ))}
            </ol>
            <div className="row end">
              <button className="primary" disabled={!plan.ok} onClick={start}>{plan.ok ? "Rebuild venv" : "Fix the failed checks first"}</button>
            </div>
          </>
        )}
        {started && (
          <>
            <p className="muted">{finished ? (finished.ok ? "Done." : "Failed. The old venv is in service.") : "Running…"}</p>
            <pre ref={logRef} className="script tall">{log}</pre>
            {finished?.ok && (
              <p>{finished.venv} is rebuilt.{finished.backup ? ` The old venv is kept as ${finished.backup}; remove it when you no longer need it.` : ""}</p>
            )}
            {finished && !finished.ok && <p className="muted">{finished.error}</p>}
          </>
        )}
      </div>
    </div>
  );
}
