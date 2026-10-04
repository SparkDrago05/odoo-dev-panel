import { Fragment, useCallback, useEffect, useState } from "react";
import { rpc } from "./rpc";

export const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as const;
export type Level = (typeof LEVELS)[number];

type Group = {
  id: string; level: Level; logger: string; title: string; exception: string | null;
  frame: { file: string; line: number; function: string } | null;
  count: number; first_line: number; last_line: number; first_time: string; last_time: string; dbs: string[]; sample: string;
};
type Analysis = { counts: Record<Level, number>; groups: Group[]; first_error_line: number | null; start_offset: number };

// Same record shape as core/src/odoo_dev_panel/logs.py; the core does the grouping, this only filters the live pane.
const RECORD = /^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} \d+ ([A-Z_]+) \S+ [^\s:]+:/;
const DOT: Record<Level, string> = { CRITICAL: "error", ERROR: "error", WARNING: "stopping", INFO: "", DEBUG: "" };

/** Keep records at `minimum` or above with their continuation lines (tracebacks); keep output before the first record. */
export function filterLog(text: string, minimum: Level): string {
  const min = LEVELS.indexOf(minimum);
  let keep: boolean | null = null;
  const out: string[] = [];
  for (const line of text.split("\n")) {
    const m = RECORD.exec(line);
    if (m && LEVELS.includes(m[1] as Level)) keep = LEVELS.indexOf(m[1] as Level) >= min;
    if (keep ?? true) out.push(line);
  }
  return out.join("\n");
}

export function Problems({ user, id, state, onError }: { user: string; id: string; state: string; onError: (message: string) => void }) {
  const [result, setResult] = useState<Analysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

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

  // Load on open, and again when the session ends (the last lines are often the interesting ones).
  useEffect(() => {
    setResult(null);
    setOpen(null);
    load();
  }, [load, state]);

  const counts = result ? LEVELS.filter((l) => l !== "DEBUG" && result.counts[l]).map((l) => `${result.counts[l]} ${l}`) : [];
  return (
    <div className="problems">
      <div className="row">
        <button onClick={load} disabled={loading}>{loading ? "Reading log…" : "Refresh"}</button>
        {result && (
          <span className="muted">
            {counts.length ? counts.join(", ") : "No Odoo log records yet"}
            {result.start_offset > 0 && " · last 8 MB of the log only; line numbers count from there"}
          </span>
        )}
      </div>
      {result && result.groups.length === 0 && <p className="muted">No warnings or errors.</p>}
      {result && result.groups.length > 0 && (
        <table>
          <tbody>
            {result.groups.map((g) => (
              <Fragment key={g.id}>
                <tr onClick={() => setOpen(open === g.id ? null : g.id)}>
                  <td><span className={`dot ${DOT[g.level]}`} /> {g.level}</td>
                  <td>×{g.count}</td>
                  <td>
                    {g.title}
                    <div className="muted">
                      {g.logger}
                      {g.frame && ` · ${g.frame.file}:${g.frame.line} in ${g.frame.function}`}
                      {g.dbs.length > 0 && ` · ${g.dbs.join(", ")}`}
                    </div>
                  </td>
                  <td className="muted">{g.count === 1 ? `line ${g.first_line}` : `lines ${g.first_line}–${g.last_line}`}<br />{g.last_time}</td>
                </tr>
                {open === g.id && (
                  <tr><td colSpan={4}><pre className="script">{g.sample}</pre></td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
