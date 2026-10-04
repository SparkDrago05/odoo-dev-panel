import { Fragment, useEffect, useRef, useState } from "react";
import { filterLog, LEVELS, type Level } from "./LogTools";
import { rpc } from "./rpc";

type Place = { container: string; host: string | null; volume: string | null; anonymous: boolean; in_image: boolean };
type Container = {
  id: string; name: string; image: string; version: string | null; status: string | null; running: boolean;
  compose: { project: string; service: string | null; working_dir: string | null; files: string[] } | null;
  ports: { container: string; host_ip: string | null; host_port: number }[];
  config: Place | null; addons: Place[]; data: Place | null;
  db: { host: string | null; port: string | null; user: string | null; container: string | null };
};
type Listing = { containers: Container[]; error: string | null; available: boolean };
type Check = { id: string; status: "ok" | "warn" | "fail"; detail: string };
type Step = { id: string; phase: number; actor: string; title: string; commands: string[] };
type Plan = { kind: string; container: string; checks: Check[]; steps: Step[]; ok: boolean };
type StepEvent = { run_id: string; step: string; status: "start" | "output" | "ok" | "fail"; text: string };
type Finished = { run_id: string; ok: boolean; error: string | null };
type Group = { id: string; level: Level; title: string; count: number; logger: string; sample: string; last_time: string };
type Action = "start" | "stop" | "restart" | "upgrade";

const LOG_LIMIT = 400_000;

const where = (p: Place) => p.host ?? (p.volume ? `${p.anonymous ? "anonymous volume" : "volume"} ${p.volume}` : "inside the image");

/** Odoo containers, read-only. Loaded on its own so a slow Docker daemon does not hold up the scan. */
export function Docker({ onError }: { onError: (message: string) => void }) {
  const [listing, setListing] = useState<Listing | null>(null);
  const [loading, setLoading] = useState(false);
  const [action, setAction] = useState<{ container: Container; action: Action } | null>(null);
  const [logsOf, setLogsOf] = useState<Container | null>(null);
  const [shellOf, setShellOf] = useState<Container | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      setListing(await rpc.request<Listing>("docker.list"));
    } catch (e) {
      onError(String((e as Error).message));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Hide the section on machines without Docker; it is not a problem there.
  if (listing && !listing.available && listing.error === "docker is not installed") return null;
  return (
    <>
      <div className="row">
        <h3>Docker</h3>
        <button onClick={load} disabled={loading}>{loading ? "Reading…" : "Refresh"}</button>
      </div>
      {listing?.error && <p className="muted">Docker: {listing.error}</p>}
      {listing && !listing.error && listing.containers.length === 0 && <p className="muted">No Odoo containers.</p>}
      {listing && listing.containers.length > 0 && (
        <table>
          <thead>
            <tr><th>Container</th><th>Odoo</th><th>State</th><th>Ports</th><th>Mounts</th><th /></tr>
          </thead>
          <tbody>
            {listing.containers.map((c) => (
              <tr key={c.id}>
                <td>
                  {c.name}
                  <div className="muted">{c.image}{c.compose ? ` · compose ${c.compose.project}/${c.compose.service ?? "?"}` : ""}</div>
                </td>
                <td>{c.version ?? "?"}</td>
                <td><span className={`dot ${c.running ? "running" : ""}`} /> {c.status}</td>
                <td>
                  {c.ports.length === 0 ? <span className="muted">none</span> : c.ports.map((p) => (
                    <div key={`${p.host_port}-${p.container}`}>
                      {c.running && p.container.startsWith("8069/")
                        ? <a href="#" onClick={(e) => { e.preventDefault(); rpc.request("run.open", { port: p.host_port }).catch((err) => onError(String(err.message))); }}>:{p.host_port}</a>
                        : `:${p.host_port}`}
                      <span className="muted"> → {p.container}</span>
                    </div>
                  ))}
                </td>
                <td className="muted">
                  {c.config && <div>config {c.config.container} = {where(c.config)}</div>}
                  {c.addons.map((a) => <div key={a.container}>addons {a.container} = {where(a)}</div>)}
                  {c.data && <div>data {where(c.data)}</div>}
                  {(c.db.container || c.db.host) && <div>db {c.db.container ?? c.db.host}{c.db.user ? ` as ${c.db.user}` : ""}</div>}
                </td>
                <td className="actions">
                  {c.running
                    ? <button onClick={() => setAction({ container: c, action: "stop" })}>Stop</button>
                    : <button onClick={() => setAction({ container: c, action: "start" })}>Start</button>}
                  <button disabled={!c.running} onClick={() => setAction({ container: c, action: "restart" })}>Restart</button>
                  <button onClick={() => setAction({ container: c, action: "upgrade" })} title="Update or install modules in a database">Upgrade…</button>
                  <button onClick={() => setLogsOf(c)}>Logs</button>
                  <button disabled={!c.running} onClick={() => setShellOf(c)} title="odoo shell needs a terminal: shows the command">Shell</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {action && <ActionDialog {...action} onClose={(changed) => { setAction(null); if (changed) load(); }} onError={onError} />}
      {logsOf && <LogsDialog container={logsOf} onClose={() => setLogsOf(null)} onError={onError} />}
      {shellOf && <ShellDialog container={shellOf} onClose={() => setShellOf(null)} onError={onError} />}
    </>
  );
}

function ActionDialog({ container, action, onClose, onError }: {
  container: Container; action: Action; onClose: (changed: boolean) => void; onError: (message: string) => void;
}) {
  const [database, setDatabase] = useState("");
  const [update, setUpdate] = useState("");
  const [install, setInstall] = useState("");
  const [dbs, setDbs] = useState<string[]>([]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [log, setLog] = useState("");
  const [started, setStarted] = useState(false);
  const [finished, setFinished] = useState<Finished | null>(null);
  const runId = useRef<string | null>(null);
  const logRef = useRef<HTMLPreElement>(null);

  const csv = (v: string) => v.split(",").map((x) => x.trim()).filter(Boolean);
  const params = () => ({ container: container.name, action, database: database || undefined, update: csv(update), install: csv(install) });

  useEffect(() => {
    if (action !== "upgrade") return;
    rpc.request<{ databases: { name: string }[] }>("db.list", { root: `docker:${container.name}` })
      .then((l) => setDbs(l.databases.map((d) => d.name))).catch(() => setDbs([]));
  }, [action, container.name]);

  useEffect(() => {
    const timer = setTimeout(() => {
      setError(null);
      rpc.request<Plan>("docker.plan", params()).then(setPlan).catch((e) => { setPlan(null); setError(String(e.message)); });
    }, 300);
    return () => clearTimeout(timer);
  }, [container.name, action, database, update, install]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const offs = [
      rpc.on("docker.step", (e: StepEvent) => {
        if (e.run_id !== runId.current) return;
        const line = e.status === "start" ? `\n== ${e.step}: ${e.text}` : e.status === "output" ? e.text
          : `== ${e.step}: ${e.status === "ok" ? "ok" : `FAILED ${e.text}`}`;
        setLog((old) => (old + line + "\n").slice(-LOG_LIMIT));
      }),
      rpc.on("docker.finished", (e: Finished) => { if (e.run_id === runId.current) setFinished(e); }),
    ];
    return () => offs.forEach((off) => off());
  }, []);
  useEffect(() => { const el = logRef.current; if (el) el.scrollTop = el.scrollHeight; }, [log]);

  const start = async () => {
    setError(null);
    try {
      const r = await rpc.request<{ run_id: string }>("docker.run", params());
      runId.current = r.run_id;
      setStarted(true);
    } catch (e) {
      setError(String((e as Error).message));
    }
  };
  const busy = started && !finished;
  const title = { start: "Start", stop: "Stop", restart: "Restart", upgrade: "Upgrade modules in" }[action];
  return (
    <div className="modal-backdrop">
      <div className="modal wide">
        <div className="row between">
          <h2>{title} {container.name}</h2>
          <button disabled={busy} onClick={() => onClose(!!finished)}>Close</button>
        </div>
        {error && <div className="error" role="alert">{error}</div>}
        {!started && (
          <>
            {action === "stop" && <p className="muted">Odoo gets up to 30 seconds to shut down cleanly.</p>}
            {action === "upgrade" && (
              <>
                <label>Database <input list="docker-dbs" value={database} onChange={(e) => setDatabase(e.target.value)} />
                  <datalist id="docker-dbs">{dbs.map((d) => <option key={d} value={d} />)}</datalist></label>
                <label>Modules to update (-u, comma separated) <input value={update} onChange={(e) => setUpdate(e.target.value)} /></label>
                <label>Modules to install (-i, comma separated) <input value={install} onChange={(e) => setInstall(e.target.value)} /></label>
                <p className="muted">Snapshot the database first (Databases panel, container {container.name}) if the data matters.</p>
              </>
            )}
            {plan && (
              <>
                <h3>Checks</h3>
                <table><tbody>
                  {plan.checks.map((c) => (
                    <tr key={c.id}>
                      <td><span className={`dot ${c.status === "ok" ? "running" : c.status === "warn" ? "stopping" : "error"}`} /> {c.status}</td>
                      <td>{c.id}</td><td className="muted">{c.detail}</td>
                    </tr>
                  ))}
                </tbody></table>
                <h3>Steps</h3>
                <ol>{plan.steps.map((s) => <li key={s.id}>{s.title}{s.commands.map((c) => <code key={c} className="command">{c}</code>)}</li>)}</ol>
                <div className="row end">
                  <button className="primary" disabled={!plan.ok} onClick={start}>{plan.ok ? title.replace(" in", "") : "Fix the failed checks first"}</button>
                </div>
              </>
            )}
          </>
        )}
        {started && (
          <>
            <p className="muted">{finished ? (finished.ok ? "Done." : "Failed.") : "Running…"}</p>
            <pre ref={logRef} className="script tall">{log}</pre>
            {finished && !finished.ok && <p className="muted">{finished.error}</p>}
          </>
        )}
      </div>
    </div>
  );
}

function LogsDialog({ container, onClose, onError }: { container: Container; onClose: () => void; onError: (message: string) => void }) {
  const [text, setText] = useState("");
  const [groups, setGroups] = useState<Group[]>([]);
  const [level, setLevel] = useState<Level>("DEBUG");
  const [view, setView] = useState<"log" | "problems">("log");
  const [follow, setFollow] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const logRef = useRef<HTMLPreElement>(null);

  const load = async () => {
    setLoading(true);
    try {
      const r = await rpc.request<{ text: string; analysis: { groups: Group[] } }>("docker.logs", { container: container.name, tail: 1000 });
      setText(r.text.slice(-LOG_LIMIT));
      setGroups(r.analysis.groups);
    } catch (e) {
      onError(String((e as Error).message));
      setFollow(false);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!follow) return;
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [follow]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { const el = logRef.current; if (el && follow) el.scrollTop = el.scrollHeight; }, [text, follow]);

  return (
    <div className="modal-backdrop">
      <div className="modal wide">
        <div className="row between">
          <h2>Logs of {container.name}</h2>
          <button onClick={onClose}>Close</button>
        </div>
        <div className="row">
          <button className={view === "log" ? "primary" : ""} onClick={() => setView("log")}>Log</button>
          <button className={view === "problems" ? "primary" : ""} onClick={() => setView("problems")}>Problems{groups.length ? ` (${groups.length})` : ""}</button>
          <button onClick={load} disabled={loading}>{loading ? "Reading…" : "Refresh"}</button>
          <label><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> Follow</label>
          {view === "log" && (
            <select value={level} onChange={(e) => setLevel(e.target.value as Level)} aria-label="Minimum level">
              {LEVELS.map((l) => <option key={l} value={l}>{l === "DEBUG" ? "all levels" : `${l} and above`}</option>)}
            </select>
          )}
          <span className="muted">last 1000 lines</span>
        </div>
        {view === "log" && <pre ref={logRef} className="script tall">{filterLog(text, level)}</pre>}
        {view === "problems" && groups.length === 0 && <p className="muted">No warnings or errors in the last 1000 lines.</p>}
        {view === "problems" && groups.length > 0 && (
          <table><tbody>
            {groups.map((g) => (
              <Fragment key={g.id}>
                <tr onClick={() => setOpen(open === g.id ? null : g.id)}>
                  <td><span className={`dot ${g.level === "WARNING" ? "stopping" : "error"}`} /> {g.level}</td>
                  <td>×{g.count}</td>
                  <td>{g.title}<div className="muted">{g.logger}</div></td>
                  <td className="muted">{g.last_time}</td>
                </tr>
                {open === g.id && <tr><td colSpan={4}><pre className="script">{g.sample}</pre></td></tr>}
              </Fragment>
            ))}
          </tbody></table>
        )}
      </div>
    </div>
  );
}

function ShellDialog({ container, onClose, onError }: { container: Container; onClose: () => void; onError: (message: string) => void }) {
  const [database, setDatabase] = useState("");
  const [dbs, setDbs] = useState<string[]>([]);
  const [command, setCommand] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    rpc.request<{ databases: { name: string }[] }>("db.list", { root: `docker:${container.name}` })
      .then((l) => setDbs(l.databases.map((d) => d.name))).catch(() => setDbs([]));
  }, [container.name]);
  useEffect(() => {
    setCopied(false);
    if (!database) { setCommand(null); return; }
    rpc.request<{ command: string }>("docker.shell", { container: container.name, database })
      .then((r) => setCommand(r.command)).catch((e) => { setCommand(null); onError(String(e.message)); });
  }, [database]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="modal-backdrop">
      <div className="modal">
        <div className="row between">
          <h2>Shell in {container.name}</h2>
          <button onClick={onClose}>Close</button>
        </div>
        <p className="muted">odoo shell is interactive and needs a terminal. Pick a database, copy the command and run it in your terminal.</p>
        <label>Database <input list="shell-dbs" value={database} onChange={(e) => setDatabase(e.target.value)} />
          <datalist id="shell-dbs">{dbs.map((d) => <option key={d} value={d} />)}</datalist></label>
        {command && (
          <>
            <code className="command">{command}</code>
            <div className="row end">
              <button onClick={() => navigator.clipboard.writeText(command).then(() => setCopied(true)).catch(() => onError("Could not copy"))}>
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
