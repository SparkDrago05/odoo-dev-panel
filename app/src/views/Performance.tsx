import { Activity, Cpu, Gauge, KeyRound, Lock, RefreshCw, ShieldCheck, XCircle } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { rpc } from "../rpc";
import { installLabel } from "../shell/Sidebar";
import { useApp } from "../state/app";
import { Dialog } from "../ui/Dialog";
import { Checks, JobView, useJob } from "../ui/Job";
import { Badge, Callout, CheckBox, EmptyState, Field, Loading, Segmented, type Tone } from "../ui/primitives";
import type { Check } from "../types";
import { Panel, View, ViewHead } from "./common";

type Finding = { id: string; area: string; level: "ok" | "info" | "warn" | "fail"; title: string; detail: string };
type PgSession = { pid: number; database: string | null; role: string | null; application: string | null; client: string | null; state: string | null;
  wait_event_type: string | null; wait_event: string | null; connected_s: number | null; xact_s: number | null; query_s: number | null;
  query: string | null; blocked_by: number[]; mine: boolean; hidden: boolean };
type OdooTree = { pid: number; user: string | null; config: string | null; instance: string | null; database: string | null; port: number | null;
  processes: number; cpu_percent: number; rss_bytes: number };
type Overview = { root: string; database: string | null; postgres_error: string | null; verdict: Finding[]; odoo: OdooTree[];
  postgres: { server: { max_connections: number; connections: number; version: string; role: string; monitor: boolean };
    sessions: PgSession[]; locks: { mode: string; granted: boolean; n: number }[] } | null };

const TONE: Record<Finding["level"], "ok" | "info" | "warn" | "bad"> = { ok: "ok", info: "info", warn: "warn", fail: "bad" };
const STATE_TONE: Record<string, Tone> = { active: "ok", "idle in transaction": "warn", "idle in transaction (aborted)": "bad", idle: "idle" };
const secs = (s: number | null) => (s == null ? "" : s >= 3600 ? `${Math.floor(s / 3600)}h${Math.floor((s % 3600) / 60)}m` : s >= 60 ? `${Math.floor(s / 60)}m${s % 60}s` : `${s}s`);

/** Z3: where a local slowdown comes from: Odoo processes, PostgreSQL connections, queries and locks, configuration. */
export function PerformanceView({ root: routeRoot }: { root?: string }) {
  const app = useApp();
  const roots = app.snap?.installations ?? [];
  const root = routeRoot ?? roots.find((i) => app.snap?.processes.some((p) => p.installation === i.root))?.root ?? roots[0]?.root ?? "";
  const dbs = app.snap?.databases.find((d) => d.installation === root)?.databases ?? [];
  const [database, setDatabase] = useState("");
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [auto, setAuto] = useState(false);
  const [filter, setFilter] = useState<"active" | "all" | "mine">("active");
  const load = useCallback(async () => {
    if (!root) return;
    setBusy(true);
    try { setData(await rpc.request<Overview>("perf.overview", { root, database: database || undefined })); setError(null); }
    catch (e) { setError(String((e as Error).message)); } finally { setBusy(false); }
  }, [root, database]);
  useEffect(() => { setData(null); load(); }, [load, app.versions.perf]);
  useEffect(() => {
    if (!auto) return;
    const t = setInterval(() => { if (!document.hidden) load(); }, 5000);
    return () => clearInterval(t);
  }, [auto, load]);
  const sessions = useMemo(() => (data?.postgres?.sessions ?? []).filter((s) => filter === "all" || (filter === "mine" ? s.mine : s.state !== "idle")), [data, filter]);
  const cancel = async (s: PgSession) => {
    if (!(await app.confirm({ title: `Cancel the query of session ${s.pid}?`, body: <>pg_cancel_backend stops the running statement; the connection stays. The Odoo request that ran it gets an error.<pre className="block">{s.query}</pre></>, confirm: "Cancel query", danger: true }))) return;
    try {
      const r = await rpc.request<{ cancelled: boolean }>("perf.cancel", { root, database: data?.database, pid: s.pid });
      app.notify(r.cancelled ? "ok" : "info", r.cancelled ? `Query of ${s.pid} cancelled` : `Session ${s.pid} had nothing to cancel`);
      load();
    } catch (e) { app.onError(String((e as Error).message)); }
  };
  const server = data?.postgres?.server;

  return (
    <View>
      <ViewHead icon={<Gauge />} title="Performance"
        subtitle="What slows an installation down right now: Odoo processes, PostgreSQL connections, long queries and locks, configuration. Read-only."
        actions={<div className="row tight">
          <select value={root} aria-label="Installation" onChange={(e) => { setDatabase(""); app.nav({ view: "perf", root: e.target.value }); }} style={{ minWidth: 200 }}>
            {roots.map((i) => <option key={i.root} value={i.root}>Odoo {i.version ?? "?"} · {installLabel(i)}</option>)}
          </select>
          <select value={database} aria-label="Connect through" title="Database to connect through (the views are server-wide)" onChange={(e) => setDatabase(e.target.value)}>
            <option value="">{data?.database ? `via ${data.database}` : "auto"}</option>
            {dbs.map((d) => <option key={d.name} value={d.name}>{d.name}</option>)}
          </select>
          <CheckBox checked={auto} onChange={setAuto}>every 5 s</CheckBox>
          <button className="btn sm" disabled={busy} onClick={load}><RefreshCw />{busy ? "Measuring…" : "Refresh"}</button>
        </div>} />
      {!roots.length ? <EmptyState icon={<Gauge />} title="No installation">Nothing to measure.</EmptyState> : (
        <>
          {error && <Callout tone="bad">{error}</Callout>}
          {!data && !error ? <Loading>Sampling CPU for a second and reading pg_stat_activity…</Loading> : data && (
            <>
              <div className="stack tight">
                {data.verdict.map((f) => (
                  <Callout key={f.id} tone={TONE[f.level]} title={<span className="row tight">{f.title}<Badge>{f.area}</Badge></span>}
                    action={f.id === "visibility" ? <button className="btn sm" onClick={() => app.setDialog({ kind: "perf-grant", root })}><KeyRound />Grant pg_monitor…</button> : undefined}>
                    {f.detail}
                  </Callout>
                ))}
              </div>
              <Panel flush title={<div className="row tight"><Cpu style={{ width: 14 }} /><h2>Odoo processes</h2><span className="xs dim">CPU over one second; memory resident</span></div>}>
                {data.odoo.length === 0 ? <p className="muted" style={{ padding: 12 }}>No Odoo process of this installation runs.</p> : (
                  <div className="list">
                    {data.odoo.map((t) => (
                      <div key={t.pid} className="list-row">
                        <span className="mono small" style={{ minWidth: 80 }}>pid {t.pid}</span>
                        <span className="small truncate grow">{t.instance ?? t.config ?? "?"}{t.database ? ` · ${t.database}` : ""}{t.port ? ` · :${t.port}` : ""}</span>
                        <div className="size-bar" style={{ width: 120 }} aria-hidden><span style={{ width: `${Math.min(100, t.cpu_percent)}%`, background: t.cpu_percent >= 80 ? "var(--warning)" : undefined }} /></div>
                        <span className="mono xs nowrap" style={{ minWidth: 70, textAlign: "right" }}>{t.cpu_percent.toFixed(1)}% CPU</span>
                        <span className="mono xs nowrap" style={{ minWidth: 80, textAlign: "right" }}>{Math.round(t.rss_bytes / 2 ** 20)} MiB</span>
                        <Badge>{t.processes} proc</Badge>
                      </div>
                    ))}
                  </div>
                )}
              </Panel>
              <Panel flush title={<div className="row tight"><Activity style={{ width: 14 }} /><h2>PostgreSQL sessions</h2>
                {server && <span className="xs dim">{server.connections} of {server.max_connections} connections · {server.version.split(" ")[0]} · as {server.role}</span>}
                {server && (server.monitor ? <Badge tone="ok"><ShieldCheck style={{ width: 11 }} />pg_monitor</Badge> : <Badge tone="warn">own sessions only</Badge>)}</div>}
                actions={<Segmented label="Show" value={filter} onChange={setFilter} options={[{ value: "active", label: "Not idle" }, { value: "mine", label: "This role" }, { value: "all", label: "All" }]} />}>
                {data.postgres_error && <Callout tone="bad">{data.postgres_error}</Callout>}
                {data.postgres && (sessions.length === 0 ? <p className="muted" style={{ padding: 12 }}>No session to show.</p> : (
                  <div className="list">
                    {sessions.map((s) => (
                      <div key={s.pid} className="list-row" style={{ alignItems: "flex-start" }}>
                        <span className="mono small" style={{ minWidth: 70 }}>{s.pid}</span>
                        <div className="stack tight grow" style={{ minWidth: 0, gap: 2 }}>
                          <span className="row tight wrap">
                            <Badge tone={STATE_TONE[s.state ?? ""] ?? "idle"}>{s.state ?? "?"}</Badge>
                            <span className="xs mono">{s.database ?? ""}</span>
                            <span className="xs dim">{s.role}{s.application ? ` · ${s.application}` : ""}</span>
                            {s.query_s != null && s.state === "active" && <Badge tone={s.query_s >= 10 ? "warn" : undefined}>{secs(s.query_s)}</Badge>}
                            {s.xact_s != null && s.xact_s >= 60 && <Badge tone="warn">transaction {secs(s.xact_s)}</Badge>}
                            {s.wait_event && <Badge mono title={s.wait_event_type ?? ""}>wait {s.wait_event}</Badge>}
                            {s.blocked_by.length > 0 && <Badge tone="bad"><Lock style={{ width: 11 }} />blocked by {s.blocked_by.join(", ")}</Badge>}
                          </span>
                          <code className="xs break dim" style={{ whiteSpace: "pre-wrap" }}>{s.hidden ? "(query hidden: another role; grant pg_monitor to see it)" : (s.query ?? "").slice(0, 600)}</code>
                        </div>
                        {s.mine && s.state === "active" && <button className="btn ghost sm" onClick={() => cancel(s)}><XCircle />Cancel query</button>}
                      </div>
                    ))}
                  </div>
                ))}
              </Panel>
              {data.postgres && data.postgres.locks.length > 0 && (
                <Panel title="Locks">
                  <div className="row tight wrap">
                    {data.postgres.locks.map((l) => <Badge key={`${l.mode}${l.granted}`} tone={l.granted ? undefined : "bad"} mono>{l.mode}{l.granted ? "" : " (waiting)"} × {l.n}</Badge>)}
                  </div>
                </Panel>
              )}
            </>
          )}
        </>
      )}
    </View>
  );
}

type GrantPlan = { root: string; role: string; revoke: boolean; script: string; checks: Check[]; ok: boolean };

/** One reviewed sudo script: GRANT (or REVOKE) pg_monitor to the installation's role. */
export function GrantMonitorDialog({ root, revoke, onClose }: { root: string; revoke?: boolean; onClose: (changed: boolean) => void }) {
  const [plan, setPlan] = useState<GrantPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rev, setRev] = useState(!!revoke);
  const job = useJob("perf");
  useEffect(() => { setPlan(null); rpc.request<GrantPlan>("perf.grant_plan", { root, revoke: rev }).then(setPlan).catch((e) => setError(String(e.message))); }, [root, rev]);
  const start = async () => {
    try { job.begin((await rpc.request<{ run_id: string }>("perf.grant_run", { root, revoke: rev })).run_id); } catch (e) { setError(String((e as Error).message)); }
  };
  return (
    <Dialog size="lg" icon={<KeyRound />} title={rev ? "Revoke pg_monitor" : "Grant pg_monitor"} subtitle={plan ? `role ${plan.role}` : root}
      onClose={() => onClose(!!job.finished?.ok)} locked={job.busy}
      footer={<>
        <button className="btn" disabled={job.busy} onClick={() => onClose(!!job.finished?.ok)}>{job.started ? "Close" : "Cancel"}</button>
        {!job.started && plan && <button className="btn primary" disabled={!plan.ok} onClick={start}>Run with sudo</button>}
      </>}>
      {error && <Callout tone="bad">{error}</Callout>}
      {!plan && !error && <Loading />}
      {plan && !job.started && (
        <div className="stack">
          <Segmented label="Action" value={rev ? "revoke" : "grant"} onChange={(v) => setRev(v === "revoke")} options={[{ value: "grant", label: "Grant" }, { value: "revoke", label: "Revoke" }]} />
          <Callout tone="info">pg_monitor lets {plan.role} read every session's activity and statistics on this PostgreSQL server (read-only; it cannot change data or stop other sessions). It is not superuser.</Callout>
          <Checks checks={plan.checks} />
          <Field label="Script that runs as root"><pre className="block">{plan.script}</pre></Field>
        </div>
      )}
      {job.started && <JobView job={job} done={rev ? "Revoked." : "Granted. Refresh Performance to see every session."} />}
    </Dialog>
  );
}
