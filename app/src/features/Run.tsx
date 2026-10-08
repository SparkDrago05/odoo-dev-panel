import { Camera, Play, TerminalSquare } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { rpc } from "../rpc";
import { useApp } from "../state/app";
import { Dialog } from "../ui/Dialog";
import { Callout, CheckBox, Cmd, Field } from "../ui/primitives";

const DEV_FLAGS: [string, string][] = [
  ["reload", "restart on Python change"], ["qweb", "reload QWeb templates"], ["werkzeug", "debugger on errors"],
  ["xml", "read views from files"], ["pdb", "pdb on exceptions"], ["all", "all of them"],
];
const csv = (value: string) => value.split(",").map((v) => v.trim()).filter(Boolean);

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

/** Launch form: instance, database, port, -u/-i, --dev flags, with the resulting command line shown before start.
 * `fixed` pins the instance (instance view); otherwise the user picks one. */
export function RuntimeControls({ fixed, onStarted, compact }: { fixed?: string; onStarted?: () => void; compact?: boolean }) {
  const app = useApp();
  const { snap, runningAgents } = app;
  const runnable = useMemo(() => (snap?.instances ?? []).filter((i) => {
    const inst = snap?.installations.find((x) => x.root === i.installation);
    return inst && inst.venv_ok !== null && inst.owner;
  }), [snap]);
  const [instance, setInstance] = useState(fixed ?? "");
  const [db, setDb] = useState("");
  const [port, setPort] = useState("");
  const [update, setUpdate] = useState("");
  const [install, setInstall] = useState("");
  const [stopAfterInit, setStopAfterInit] = useState(false);
  const [dev, setDev] = useState<string[]>([]);
  const [starting, setStarting] = useState(false);
  const [snapshot, setSnapshot] = useState(false);
  const [status, setStatus] = useState("");

  useEffect(() => { if (fixed) setInstance(fixed); }, [fixed]);
  useEffect(() => { if (!instance && runnable.length) setInstance(runnable[0].path); }, [runnable.length, instance]); // eslint-disable-line react-hooks/exhaustive-deps

  const current = runnable.find((i) => i.path === instance);
  const installation = snap?.installations.find((x) => x.root === current?.installation);
  const owner = installation?.owner ?? null;
  const dbs = snap?.databases.find((d) => d.installation === current?.installation)?.databases ?? [];
  const agentUp = owner !== null && runningAgents.has(owner);
  const oneShot = update.trim() !== "" || install.trim() !== "";

  // Mirrors run.build_argv in the core; the port shows as "auto" until the core picks a free one.
  const preview = useMemo(() => {
    if (!current || !installation) return "";
    const argv = [installation.venv_python ?? `${installation.root}/venv/bin/python`, `${installation.source}/odoo-bin`, "-c", current.path];
    if (db) argv.push("-d", db);
    argv.push("--http-port", port || "<auto>");
    if (csv(update).length) argv.push("-u", csv(update).join(","));
    if (csv(install).length) argv.push("-i", csv(install).join(","));
    if (stopAfterInit) argv.push("--stop-after-init");
    if (dev.length) argv.push("--dev=" + dev.join(","));
    return argv.join(" ");
  }, [current, installation, db, port, update, install, stopAfterInit, dev]);

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
      await app.startSession(owner, session.id);
      onStarted?.();
    } catch (e) {
      setStatus("");
      app.onError(String((e as Error).message));
    } finally {
      setStarting(false);
    }
  };

  if (!snap) return null;
  if (runnable.length === 0) return <Callout tone="info">No runnable instance found: an instance needs an installation with a venv and a run-as user.</Callout>;
  return (
    <div className="stack">
      <div className={compact ? "grid3" : "grid3"}>
        {!fixed && (
          <Field label="Instance">
            <select value={instance} onChange={(e) => { setInstance(e.target.value); setDb(""); }}>
              {runnable.map((i) => <option key={i.path} value={i.path}>{i.name} · {i.installation}</option>)}
            </select>
          </Field>
        )}
        <Field label="Database" hint={dbs.length ? `${dbs.length} known` : "optional"}>
          <input list="run-dbs" className="mono" value={db} onChange={(e) => setDb(e.target.value)} placeholder="from the config" />
        </Field>
        <datalist id="run-dbs">{dbs.map((d) => <option key={d.name} value={d.name} />)}</datalist>
        <Field label="HTTP port" hint="empty: first free">
          <input className="mono" value={port} onChange={(e) => setPort(e.target.value.replace(/\D/g, ""))} placeholder="auto" />
        </Field>
      </div>
      <div className="grid2">
        <Field label={<>Update modules <code className="dim">-u</code></>} hint="comma separated">
          <input className="mono" value={update} onChange={(e) => setUpdate(e.target.value)} placeholder="sale_custom, hr_extra" />
        </Field>
        <Field label={<>Install modules <code className="dim">-i</code></>} hint="comma separated">
          <input className="mono" value={install} onChange={(e) => setInstall(e.target.value)} placeholder="module_name" />
        </Field>
      </div>
      <div className="row">
        <CheckBox checked={stopAfterInit} onChange={setStopAfterInit}><code>--stop-after-init</code></CheckBox>
        {update.trim() !== "" && (
          <CheckBox checked={snapshot} onChange={setSnapshot} title="Back up the database into the snapshot folder before the upgrade (Databases: Revert)">
            <Camera style={{ width: 13, height: 13, verticalAlign: -2 }} /> Snapshot the database first
          </CheckBox>
        )}
      </div>
      <Field label={<code>--dev</code>}>
        <div className="row tight">
          {DEV_FLAGS.map(([f, hint]) => (
            <button key={f} type="button" className="chip" aria-pressed={dev.includes(f)} title={hint}
              onClick={() => setDev(dev.includes(f) ? dev.filter((x) => x !== f) : [...dev, f])}>{f}</button>
          ))}
        </div>
      </Field>
      {preview && (
        <Field label={<>Command <span className="dim">· runs as {owner}</span></>}><Cmd>{preview}</Cmd></Field>
      )}
      {!agentUp && owner && (
        <Callout tone="warn" action={<button className="btn sm primary" onClick={() => app.act(`unlocking ${owner}`, () => rpc.request("agent.start", { user: owner }))}>Unlock {owner}</button>}>
          The {owner} agent is locked. Unlock it to start Odoo as {owner} (one sudo prompt).
        </Callout>
      )}
      {oneShot && !db && <Callout tone="info">-u and -i need a database.</Callout>}
      {oneShot && !stopAfterInit && <span className="small muted">Without --stop-after-init the server keeps serving after the upgrade.</span>}
      {status && <span className="small muted">{status}</span>}
      <div className="row end">
        <button className="btn" disabled={!agentUp || starting || !db} onClick={() => start(true)} title="odoo-bin shell on the selected database">
          <TerminalSquare />Shell
        </button>
        <button className="btn primary" disabled={!agentUp || starting || (oneShot && !db)} onClick={() => start()}>
          <Play />{starting ? "Starting…" : oneShot ? "Run" : "Start"}
        </button>
      </div>
    </div>
  );
}

export function RunDialog({ instance, onClose }: { instance?: string; onClose: () => void }) {
  return (
    <Dialog size="lg" icon={<Play />} title="Run Odoo" subtitle={instance} onClose={onClose}>
      <RuntimeControls fixed={instance} onStarted={onClose} />
    </Dialog>
  );
}
