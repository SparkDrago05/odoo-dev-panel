import { useEffect, useRef, useState } from "react";
import { rpc } from "./rpc";

type Db = { name: string; size: number; filestore: string | null; filestore_exists: boolean | null };
type Listing = { databases: Db[]; error: string | null; recipes: string[]; agent_running: boolean | null };
type Check = { id: string; status: "ok" | "warn" | "fail"; detail: string };
type Step = { id: string; phase: number; actor: string; title: string; commands: string[] };
type Plan = { kind: string; root: string; run_as: string; checks: Check[]; steps: Step[]; ok: boolean; recipe_sum: string | null };
type Action = "backup" | "restore" | "clone" | "drop" | "neutralize" | "snapshot" | "revert" | "forget";
type Params = { source?: string; target?: string; backup?: string; dest?: string; recipe?: string; confirm?: string; keep?: number };
type Snap = { path: string; name: string; database: string | null; created_at: string | null; bytes: number | null; filestore: boolean };
type StepEvent = { run_id: string; step: string; status: "start" | "output" | "ok" | "fail"; text: string };
type Finished = {
  run_id: string; ok: boolean; error: string | null; receipt?: string; backup?: string; trash?: string;
  aside?: string; aside_fs?: string | null; pruned?: string[];
};

const LOG_LIMIT = 400_000;
const mb = (n: number) => `${(n / 1e6).toFixed(1)} MB`;

export default function Databases({ onError }: { onError: (message: string) => void }) {
  const [roots, setRoots] = useState<{ root: string; version: string | null }[]>([]);
  const [root, setRoot] = useState("");
  const [listing, setListing] = useState<Listing | null>(null);
  const [dialog, setDialog] = useState<{ action: Action; source?: string; backup?: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [snaps, setSnaps] = useState<{ snapshots: Snap[]; error: string | null } | null>(null);

  useEffect(() => {
    rpc.request<{ installations: { root: string; version: string | null }[] }>("discover.scan", { no_databases: true })
      .then((s) => { setRoots(s.installations); if (s.installations[0]) setRoot(s.installations[0].root); })
      .catch((e) => onError(String(e.message)));
  }, []);

  const load = async () => {
    if (!root) return;
    setLoading(true);
    try {
      const l = await rpc.request<Listing>("db.list", { root });
      setListing(l);
      // Snapshots are read through the run-as user's agent; without it the list stays empty.
      setSnaps(l.agent_running === false ? { snapshots: [], error: "unlock the agent to list snapshots" }
        : await rpc.request<{ snapshots: Snap[]; error: string | null }>("db.snapshots", { root }));
    } catch (e) {
      onError(String((e as Error).message));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { setListing(null); setSnaps(null); if (root) load(); }, [root]);

  return (
    <section>
      <h2>Databases</h2>
      <div className="row">
        <select value={root} onChange={(e) => setRoot(e.target.value)}>
          {roots.map((r) => <option key={r.root} value={r.root}>{r.root}{r.version ? ` (Odoo ${r.version})` : ""}</option>)}
        </select>
        <button onClick={load} disabled={loading || !root}>{loading ? "Loading…" : "Refresh"}</button>
        <button disabled={!root} onClick={() => setDialog({ action: "restore" })}>Restore backup…</button>
      </div>
      {listing?.error && <p className="muted">Databases could not be listed: {listing.error}</p>}
      {listing && (
        <table>
          <tbody>
            {listing.databases.map((d) => (
              <tr key={d.name}>
                <td>{d.name}</td>
                <td className="muted">{mb(d.size)}</td>
                <td className="muted">
                  <span className={`dot ${d.filestore_exists ? "running" : d.filestore_exists === false ? "stopping" : ""}`} />{" "}
                  {d.filestore_exists ? "filestore" : d.filestore_exists === false ? "no filestore" : "filestore unknown"}
                </td>
                <td className="actions">
                  <button onClick={() => setDialog({ action: "clone", source: d.name })}>Clone</button>
                  <button onClick={() => setDialog({ action: "backup", source: d.name })}>Backup</button>
                  <button onClick={() => setDialog({ action: "snapshot", source: d.name })} title="Backup into the snapshot folder; only the newest few are kept">Snapshot</button>
                  <button onClick={() => setDialog({ action: "neutralize", source: d.name })}>Neutralize</button>
                  <button onClick={() => setDialog({ action: "drop", source: d.name })}>Drop</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {listing && listing.databases.length === 0 && !listing.error && <p className="muted">No databases owned by this installation's role.</p>}
      {snaps && (
        <>
          <h3>Snapshots</h3>
          {snaps.error && <p className="muted">Snapshots could not be listed: {snaps.error}</p>}
          {!snaps.error && snaps.snapshots.length === 0 && <p className="muted">No snapshots. Snapshot a database above, or tick "Snapshot first" on an upgrade run.</p>}
          {snaps.snapshots.length > 0 && (
            <table>
              <tbody>
                {snaps.snapshots.map((x) => (
                  <tr key={x.path}>
                    <td>{x.database}</td>
                    <td className="muted">{x.created_at ? new Date(x.created_at).toLocaleString() : x.name}</td>
                    <td className="muted">{x.bytes !== null ? mb(x.bytes) : ""}{x.filestore ? "" : " (no filestore)"}</td>
                    <td className="actions">
                      <button disabled={!listing?.databases.some((d) => d.name === x.database)}
                        title="Replace the database with this snapshot; the current one is kept under a new name"
                        onClick={() => setDialog({ action: "revert", source: x.database ?? undefined, backup: x.path })}>Revert…</button>
                      <button onClick={() => setDialog({ action: "restore", backup: x.path })}>Restore as new…</button>
                      <button onClick={() => setDialog({ action: "forget", backup: x.path })}>Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
      {dialog && listing && (
        <ActionDialog root={root} action={dialog.action} source={dialog.source} initialBackup={dialog.backup} recipes={listing.recipes}
          onClose={(changed) => { setDialog(null); if (changed) load(); }} />
      )}
    </section>
  );
}

function ActionDialog({ root, action, source, initialBackup, recipes, onClose }: {
  root: string; action: Action; source?: string; initialBackup?: string; recipes: string[]; onClose: (changed: boolean) => void;
}) {
  const [target, setTarget] = useState("");
  const [backup, setBackup] = useState(initialBackup ?? "");
  const [keep, setKeep] = useState("3");
  const [dest, setDest] = useState("");
  const [recipe, setRecipe] = useState("default");
  const [neutralize, setNeutralize] = useState(false);
  const [confirm, setConfirm] = useState("");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [log, setLog] = useState("");
  const [started, setStarted] = useState(false);
  const [finished, setFinished] = useState<Finished | null>(null);
  const runId = useRef<string | null>(null);
  const logRef = useRef<HTMLPreElement>(null);
  const modalRef = useRef<HTMLDivElement>(null);

  const params = (): Params => ({
    source, target: target || undefined, backup: backup || undefined, dest: dest || undefined,
    recipe: action === "neutralize" || (action === "clone" && neutralize) ? recipe : undefined,
    confirm: confirm || undefined,
    keep: action === "snapshot" && keep ? Number(keep) : undefined,
  });

  useEffect(() => {
    const timer = setTimeout(() => {
      setError(null);
      rpc.request<Plan>("db.plan", { root, action, ...params() }).then(setPlan).catch((e) => setError(String(e.message)));
    }, 300);
    return () => clearTimeout(timer);
  }, [root, action, source, target, backup, dest, recipe, neutralize, confirm, keep]);

  useEffect(() => {
    const offs = [
      rpc.on("db.step", (e: StepEvent) => {
        if (e.run_id !== runId.current) return;
        const line = e.status === "start" ? `\n== ${e.step}: ${e.text}` : e.status === "output" ? e.text
          : `== ${e.step}: ${e.status === "ok" ? "ok" : `FAILED ${e.text}`}`;
        setLog((old) => (old + line + "\n").slice(-LOG_LIMIT));
      }),
      rpc.on("db.finished", (e: Finished) => { if (e.run_id === runId.current) setFinished(e); }),
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
      const r = await rpc.request<{ run_id: string }>("db.run", { root, action, ...params() });
      runId.current = r.run_id;
      setStarted(true);
      // The plan view is long; without this the dialog stays scrolled down and hides the status and Close.
      modalRef.current?.scrollTo({ top: 0 });
    } catch (e) {
      setError(String((e as Error).message));
    }
  };

  const destructive = action === "drop" || action === "neutralize" || action === "revert";
  const busy = started && !finished;
  // Typed confirmation is its own field, so it is not reported as a failed check while the user is still typing.
  const hidden = destructive && !confirm ? "confirm" : "";
  const title = {
    backup: "Back up", restore: "Restore a backup", clone: "Clone", drop: "Drop", neutralize: "Neutralize",
    snapshot: "Snapshot", revert: "Revert", forget: "Delete snapshot",
  }[action];
  return (
    <div className="modal-backdrop">
      <div className="modal wide" ref={modalRef}>
        <div className="row between">
          <h2>{title}{source ? ` ${source}` : ""}</h2>
          <button disabled={busy} onClick={() => onClose(!!finished)}>Close</button>
        </div>
        {error && <div className="error" role="alert">{error}</div>}
        {!started && (
          <>
            {action === "clone" && (
              <>
                <label>New database name <input value={target} onChange={(e) => setTarget(e.target.value)} /></label>
                <label>
                  <input type="checkbox" checked={neutralize} onChange={(e) => setNeutralize(e.target.checked)} /> Run a neutralization recipe on the clone
                </label>
              </>
            )}
            {action === "backup" && (
              <label>Folder for backups (empty: the run-as user's home/odp-backups) <input value={dest} onChange={(e) => setDest(e.target.value)} /></label>
            )}
            {action === "restore" && (
              <>
                <label>Backup folder (absolute path) <input value={backup} onChange={(e) => setBackup(e.target.value)} /></label>
                <label>New database name <input value={target} onChange={(e) => setTarget(e.target.value)} /></label>
              </>
            )}
            {(action === "neutralize" || (action === "clone" && neutralize)) && (
              <label>Recipe <select value={recipe} onChange={(e) => setRecipe(e.target.value)}>{recipes.map((r) => <option key={r}>{r}</option>)}</select></label>
            )}
            {action === "snapshot" && (
              <>
                <p className="muted">A backup in the run-as user's home/odp-backups/snapshots. Older snapshots of {source} beyond the number kept are removed; manual backups are never touched.</p>
                <label>Snapshots of {source} to keep <input value={keep} onChange={(e) => setKeep(e.target.value.replace(/\D/g, ""))} size={4} /></label>
              </>
            )}
            {action === "revert" && (
              <p className="muted">{source} is replaced by the snapshot {initialBackup}. Nothing is deleted: the current {source} and its filestore are kept under a new name (shown in the checks); drop them when you no longer need them.</p>
            )}
            {action === "forget" && <p className="muted">The snapshot folder {initialBackup} is removed. This cannot be undone.</p>}
            {action === "drop" && (
              <p className="muted">The database is dropped. Its filestore is moved to a .trash-… folder next to it, not deleted.</p>
            )}
            {action === "neutralize" && (
              <p className="muted">This changes the data of {source} in place (passwords, schedules, mail servers). Clone first if this is a client database.</p>
            )}
            {destructive && (
              <label>Type <b>{source}</b> to confirm <input value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="off" /></label>
            )}
            {plan && (
              <>
                <h3>Checks</h3>
                <table><tbody>
                  {plan.checks.filter((c) => c.id !== hidden).map((c) => (
                    <tr key={c.id}>
                      <td><span className={`dot ${c.status === "ok" ? "running" : c.status === "warn" ? "stopping" : "error"}`} /> {c.status}</td>
                      <td>{c.id}</td><td className="muted">{c.detail}</td>
                    </tr>
                  ))}
                </tbody></table>
                <h3>Steps</h3>
                <ol>{plan.steps.map((s) => <li key={s.id}>{s.title}{s.commands.map((c) => <code key={c} className="command">{c}</code>)}</li>)}</ol>
                <div className="row end">
                  <button className="primary" disabled={!plan.ok} onClick={start}>{plan.ok ? title : "Fix the failed checks first"}</button>
                </div>
              </>
            )}
          </>
        )}
        {started && (
          <>
            <p className="muted">{finished ? (finished.ok ? "Done." : "Failed.") : "Running…"}</p>
            <pre ref={logRef} className="script tall">{log}</pre>
            {finished?.ok && finished.backup && <p>Backup: {finished.backup}</p>}
            {finished?.ok && finished.trash && <p>Filestore moved to {finished.trash}. Remove it when you no longer need it.</p>}
            {finished?.ok && finished.aside && <p>The previous database is kept as {finished.aside}{finished.aside_fs ? `, its filestore as ${finished.aside_fs}` : ""}.</p>}
            {finished?.ok && finished.pruned && finished.pruned.length > 0 && <p className="muted">Older snapshots removed: {finished.pruned.join(", ")}</p>}
            {finished && !finished.ok && <p className="muted">{finished.error}</p>}
          </>
        )}
      </div>
    </div>
  );
}
