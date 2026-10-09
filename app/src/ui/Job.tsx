import { AlertTriangle, CheckCircle2, ChevronRight, XCircle } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { rpc } from "../rpc";
import type { Check, Step, StepEvent } from "../types";
import { LogConsole } from "./LogConsole";
import { Cmd } from "./primitives";

const LOG_LIMIT = 400_000;
const CHECK_ICON = { ok: CheckCircle2, warn: AlertTriangle, fail: XCircle };

/** Preflight checks of a plan. Every check is shown; failed ones block the job. */
export function Checks({ checks }: { checks: Check[] }) {
  if (!checks.length) return null;
  return (
    <div className="stack tight">
      <span className="section-title">Checks</span>
      <div className="checks">
        {checks.map((c) => {
          const Icon = CHECK_ICON[c.status];
          return (
            <div key={c.id} className={`check-row ${c.status}`}>
              <Icon aria-label={c.status} />
              <span className="id">{c.id}</span>
              <span className="detail">{c.detail}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** The steps a job will run, with the exact commands. */
export function Steps({ steps, actors }: { steps: Step[]; actors?: boolean }) {
  if (!steps.length) return null;
  return (
    <div className="stack tight">
      <span className="section-title">Steps</span>
      <ol className="steps">
        {steps.map((s) => (
          <li key={s.id}>
            <div>
              <span>{actors && <span className="actor">[{s.actor}] </span>}{s.title}</span>
              {s.commands.map((c) => <Cmd key={c}>{c}</Cmd>)}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function Disclosure({ title, children, open }: { title: ReactNode; children: ReactNode; open?: boolean }) {
  return (
    <details className="disclosure" open={open}>
      <summary><ChevronRight />{title}</summary>
      {children}
    </details>
  );
}

export type Finished = { run_id: string; ok: boolean; error: string | null } & Record<string, unknown>;

/** Step and finish events of one background job (`<prefix>.step`, `<prefix>.finished`). */
export function useJob<F extends Finished = Finished>(prefix: "db" | "docker" | "repair" | "provision" | "git" | "modules" | "python") {
  const [log, setLog] = useState("");
  const [started, setStarted] = useState(false);
  const [current, setCurrent] = useState<string | null>(null);
  const [finished, setFinished] = useState<F | null>(null);
  const runId = useRef<string | null>(null);
  useEffect(() => {
    const offs = [
      rpc.on(`${prefix}.step`, (e: StepEvent) => {
        if (e.run_id !== runId.current) return;
        if (e.status === "start") setCurrent(e.step);
        const line = e.status === "start" ? `\n== ${e.step}: ${e.text}` : e.status === "output" ? e.text
          : `== ${e.step}: ${e.status === "ok" ? "ok" : `FAILED ${e.text}`}`;
        setLog((old) => (old + line + "\n").slice(-LOG_LIMIT));
      }),
      rpc.on(`${prefix}.finished`, (e: F) => { if (e.run_id === runId.current) setFinished(e); }),
    ];
    return () => offs.forEach((off) => off());
  }, [prefix]);
  const begin = (id: string) => { runId.current = id; setLog(""); setFinished(null); setStarted(true); };
  const reset = () => { runId.current = null; setLog(""); setFinished(null); setStarted(false); setCurrent(null); };
  return { log, started, finished, current, begin, reset, busy: started && !finished };
}

/** Running job: status line, live log, then the result. */
export function JobView({ job, running, done, failed, children }: {
  job: { log: string; finished: Finished | null; current: string | null };
  running?: ReactNode; done?: ReactNode; failed?: ReactNode; children?: ReactNode;
}) {
  const f = job.finished;
  return (
    <div className="stack">
      <div className={`job-status${f ? (f.ok ? " ok" : " bad") : ""}`} role="status">
        {f ? (f.ok ? <CheckCircle2 /> : <XCircle />) : <span className="spinner" />}
        <span className="grow">
          {f ? (f.ok ? done ?? "Done." : failed ?? "Failed.") : running ?? `Running${job.current ? `: ${job.current}` : ""}…`}
        </span>
      </div>
      {f && !f.ok && f.error && <p className="muted break">{f.error}</p>}
      {children}
      <div className="job-log"><LogConsole text={job.log} jobs empty="Waiting for the first step…" /></div>
    </div>
  );
}
