import { useEffect, useState } from "react";
import { rpc } from "./rpc";

type Installation = { root: string; version: string | null; owner: string | null; venv_ok: boolean | null };
type Instance = { path: string; name: string; installation: string | null };
type DbEntry = { installation: string; databases: { name: string }[] };
type Snapshot = { installations: Installation[]; instances: Instance[]; databases: DbEntry[] };

const DEV_FLAGS = ["reload", "qweb", "werkzeug", "xml", "pdb", "all"];
const csv = (value: string) => value.split(",").map((v) => v.trim()).filter(Boolean);

export default function Run({ agents, onStarted, onError }: {
  agents: string[];
  onStarted: (user: string, id: string) => void;
  onError: (message: string) => void;
}) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [instance, setInstance] = useState("");
  const [db, setDb] = useState("");
  const [port, setPort] = useState("");
  const [update, setUpdate] = useState("");
  const [install, setInstall] = useState("");
  const [stopAfterInit, setStopAfterInit] = useState(false);
  const [dev, setDev] = useState<string[]>([]);
  const [starting, setStarting] = useState(false);
  const [snapshot, setSnapshot] = useState(false);
  const [status, setStatus] = useState("");

  useEffect(() => {
    rpc.request<Snapshot>("discover.scan").then(setSnap).catch((e) => onError(String(e.message)));
  }, [onError]);

  const runnable = (snap?.instances ?? []).filter((i) => {
    const inst = snap?.installations.find((x) => x.root === i.installation);
    return inst && inst.venv_ok !== null && inst.owner;
  });
  useEffect(() => {
    if (!instance && runnable.length) setInstance(runnable[0].path);
  }, [runnable.length, instance]); // eslint-disable-line react-hooks/exhaustive-deps

  const current = runnable.find((i) => i.path === instance);
  const owner = snap?.installations.find((x) => x.root === current?.installation)?.owner ?? null;
  const dbs = snap?.databases.find((d) => d.installation === current?.installation)?.databases ?? [];
  const agentUp = owner !== null && agents.includes(owner);
  const oneShot = update.trim() !== "" || install.trim() !== "";

  const start = async (shell = false) => {
    if (!current || !owner) return;
    setStarting(true);
    try {
      if (!shell && snapshot && update.trim() && db && current.installation) {
        setStatus(`Snapshot of ${db}…`);
        const done = await snapshotFirst(current.installation, db);
        if (!done.ok) throw new Error(`Snapshot failed, nothing started: ${done.error}`);
        setStatus(`Snapshot: ${done.backup}`);
      }
      const session = await rpc.request<{ id: string }>("run.start", shell ? { instance: current.path, db, shell: true } : {
        instance: current.path,
        db: db || undefined,
        http_port: port ? Number(port) : undefined,
        update: csv(update),
        install: csv(install),
        stop_after_init: stopAfterInit,
        dev,
      });
      onStarted(owner, session.id);
    } catch (e) {
      setStatus("");
      onError(String((e as Error).message));
    } finally {
      setStarting(false);
    }
  };

  return (
    <section>
      <h2>Run</h2>
      {!snap ? <p className="muted">Scanning…</p> : runnable.length === 0 ? <p className="muted">No runnable instance found.</p> : (
        <>
          <div className="row">
            <select value={instance} onChange={(e) => { setInstance(e.target.value); setDb(""); }} aria-label="Instance">
              {runnable.map((i) => <option key={i.path} value={i.path}>{i.name} ({i.installation})</option>)}
            </select>
            <input list="run-dbs" value={db} onChange={(e) => setDb(e.target.value)} placeholder="database (optional)" aria-label="Database" />
            <datalist id="run-dbs">{dbs.map((d) => <option key={d.name} value={d.name} />)}</datalist>
            <input value={port} onChange={(e) => setPort(e.target.value.replace(/\D/g, ""))} placeholder="port (auto)" size={10} aria-label="HTTP port" />
          </div>
          <div className="row">
            <input value={update} onChange={(e) => setUpdate(e.target.value)} placeholder="-u modules, comma separated" aria-label="Update modules" />
            <input value={install} onChange={(e) => setInstall(e.target.value)} placeholder="-i modules, comma separated" aria-label="Install modules" />
            <label><input type="checkbox" checked={stopAfterInit} onChange={(e) => setStopAfterInit(e.target.checked)} /> --stop-after-init</label>
            {update.trim() !== "" && (
              <label title="Back up the database into the snapshot folder before the upgrade (Databases panel: Revert)">
                <input type="checkbox" checked={snapshot} onChange={(e) => setSnapshot(e.target.checked)} /> Snapshot first
              </label>
            )}
          </div>
          <div className="row">
            <span className="muted">--dev</span>
            {DEV_FLAGS.map((f) => (
              <label key={f}>
                <input type="checkbox" checked={dev.includes(f)}
                  onChange={(e) => setDev(e.target.checked ? [...dev, f] : dev.filter((x) => x !== f))} /> {f}
              </label>
            ))}
            <button className="primary" style={{ marginLeft: "auto" }} disabled={!agentUp || starting || ((oneShot) && !db)} onClick={() => start()}>
              {oneShot ? "Run" : "Start"}
            </button>
            <button disabled={!agentUp || starting || !db} onClick={() => start(true)} title="odoo-bin shell on the selected database">
              Shell
            </button>
          </div>
          {status && <p className="muted">{status}</p>}
          {!agentUp && owner && <p className="muted">Unlock the {owner} agent first.</p>}
          {oneShot && !db && <p className="muted">-u and -i need a database.</p>}
          {oneShot && !stopAfterInit && <p className="muted">Without --stop-after-init the server keeps serving after the upgrade.</p>}
        </>
      )}
    </section>
  );
}

type SnapshotDone = { run_id: string; ok: boolean; error: string | null; backup?: string };

/** Run a database snapshot job and wait for its end. */
async function snapshotFirst(root: string, database: string): Promise<SnapshotDone> {
  let runId: string | null = null;
  let early: SnapshotDone | null = null;
  let resolve: (d: SnapshotDone) => void = () => {};
  const finished = new Promise<SnapshotDone>((r) => { resolve = r; });
  // Listen before the request: the job may finish before the request returns its run_id.
  const off = rpc.on("db.finished", (e: SnapshotDone) => {
    if (runId === null) early = e;
    else if (e.run_id === runId) resolve(e);
  });
  try {
    const r = await rpc.request<{ run_id: string }>("db.run", { root, action: "snapshot", source: database });
    runId = r.run_id;
    const pending = early as SnapshotDone | null;
    if (pending && pending.run_id === runId) return pending;
    return await finished;
  } finally {
    off();
  }
}
