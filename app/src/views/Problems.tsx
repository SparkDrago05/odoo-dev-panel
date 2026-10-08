import { AlertTriangle, ChevronRight, RefreshCw, XCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { rpc } from "../rpc";
import { useApp } from "../state/app";
import type { Level } from "../ui/logparse";
import { CopyButton, EmptyState, Loading } from "../ui/primitives";

export type ProblemGroup = {
  id: string; level: Level; logger: string; title: string; exception?: string | null;
  frame?: { file: string; line: number; function: string } | null;
  count: number; first_line?: number; last_line?: number; first_time?: string; last_time: string; dbs?: string[]; sample: string;
};
type Analysis = { counts: Record<Level, number>; groups: ProblemGroup[]; first_error_line: number | null; start_offset: number };

/** Warnings and errors of a log, grouped (the core does the grouping). */
export function ProblemList({ groups }: { groups: ProblemGroup[] }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!groups.length) return <EmptyState icon={<AlertTriangle />} title="No warnings or errors">The log has no WARNING, ERROR or CRITICAL records.</EmptyState>;
  return (
    <div className="list">
      {groups.map((g) => {
        const expanded = open === g.id;
        return (
          <div key={g.id} style={{ borderBottom: "1px solid var(--border)" }}>
            <div className={`finding ${g.level === "WARNING" ? "warning" : "error"}`} style={{ borderBottom: 0 }} role="button" tabIndex={0} aria-expanded={expanded}
              onClick={() => setOpen(expanded ? null : g.id)} onKeyDown={(e) => { if (e.key === "Enter") setOpen(expanded ? null : g.id); }}>
              {g.level === "WARNING" ? <AlertTriangle /> : <XCircle />}
              <div className="stack tight" style={{ gap: 3 }}>
                <span className="t">{g.title}</span>
                <span className="d mono">
                  {g.logger}{g.frame && ` · ${g.frame.file}:${g.frame.line} in ${g.frame.function}`}{g.dbs?.length ? ` · ${g.dbs.join(", ")}` : ""}
                </span>
              </div>
              <div className="row nowrap tight">
                <span className="badge mono">×{g.count}</span>
                <ChevronRight style={{ width: 14, height: 14, transform: expanded ? "rotate(90deg)" : undefined, transition: "transform 0.18s", color: "var(--text-3)" }} />
              </div>
            </div>
            {expanded && (
              <div style={{ padding: "0 12px 12px 40px" }} className="stack tight">
                <span className="xs dim">
                  {g.first_line !== undefined && (g.count === 1 ? `line ${g.first_line}` : `lines ${g.first_line}–${g.last_line}`)}{g.last_time && ` · last at ${g.last_time}`}
                </span>
                <div style={{ position: "relative" }}>
                  <pre className="block">{g.sample}</pre>
                  <div style={{ position: "absolute", top: 4, right: 4 }}><CopyButton text={g.sample} label="Copy traceback" /></div>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Problems view of a session log. Reloads when the session ends (the last lines are often the interesting ones). */
export function Problems({ user, id, state }: { user: string; id: string; state: string }) {
  const { onError } = useApp();
  const [result, setResult] = useState<Analysis | null>(null);
  const [loading, setLoading] = useState(false);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      setResult(await rpc.request<Analysis>("session.problems", { user, id }));
    } catch (e) {
      onError(String((e as Error).message));
    } finally {
      setLoading(false);
    }
  }, [user, id, onError]);
  useEffect(() => { setResult(null); load(); }, [load, state]);

  const counts = result ? (["CRITICAL", "ERROR", "WARNING", "INFO"] as Level[]).filter((l) => result.counts[l]).map((l) => `${result.counts[l]} ${l}`) : [];
  return (
    <div className="stack tight" style={{ gap: 0 }}>
      <div className="term-bar">
        <span className="small muted">
          {result ? (counts.length ? counts.join(" · ") : "No Odoo log records yet") : "Reading log…"}
          {result && result.start_offset > 0 && " · last 8 MB of the log only; line numbers count from there"}
        </span>
        <span className="grow" />
        <button className="btn ghost sm" onClick={load} disabled={loading}><RefreshCw />{loading ? "Reading…" : "Refresh"}</button>
      </div>
      {!result ? <Loading>Reading log…</Loading> : <ProblemList groups={result.groups} />}
    </div>
  );
}
