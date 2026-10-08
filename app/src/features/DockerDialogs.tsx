import { Container as ContainerIcon, Database, Play, RefreshCw, RotateCw, ScrollText, Square, TerminalSquare, Trash2, Wrench } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { rpc } from "../rpc";
import type { DockerAction } from "../state/app";
import type { Check, Container, DockerListing, Step } from "../types";
import { Dialog } from "../ui/Dialog";
import { Checks, JobView, Steps, useJob } from "../ui/Job";
import { LogConsole } from "../ui/LogConsole";
import { Callout, CheckBox, Cmd, Field, Loading, Segmented } from "../ui/primitives";
import { ProblemList, type ProblemGroup } from "../views/Problems";

type Plan = { kind: string; container: string; checks: Check[]; steps: Step[]; ok: boolean };
type NewPlan = Plan & { name: string; version: string; port: number | null; folder: string };

const csv = (v: string) => v.split(",").map((x) => x.trim()).filter(Boolean);
const ACTION: Record<DockerAction, { title: string; button: string; icon: ReactNode }> = {
  start: { title: "Start", button: "Start", icon: <Play /> },
  stop: { title: "Stop", button: "Stop", icon: <Square /> },
  restart: { title: "Restart", button: "Restart", icon: <RotateCw /> },
  upgrade: { title: "Upgrade modules in", button: "Upgrade", icon: <Wrench /> },
  newdb: { title: "Create a database in", button: "Create database", icon: <Database /> },
};

function useDockerDbs(container: Container, enabled = true) {
  const [dbs, setDbs] = useState<string[]>([]);
  useEffect(() => {
    if (!enabled) return;
    rpc.request<{ databases: { name: string }[] }>("db.list", { root: `docker:${container.name}` })
      .then((l) => setDbs(l.databases.map((d) => d.name))).catch(() => setDbs([]));
  }, [container.name, enabled]);
  return dbs;
}

export function DockerActionDialog({ container, action, onClose }: { container: Container; action: DockerAction; onClose: (changed: boolean) => void }) {
  const [database, setDatabase] = useState("");
  const [update, setUpdate] = useState("");
  const [install, setInstall] = useState("");
  const [demo, setDemo] = useState(true);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const job = useJob("docker");
  const newdb = action === "newdb";
  const dbs = useDockerDbs(container, action === "upgrade" || newdb);
  // "New database" is an install of base into a database that does not exist yet.
  const rpcAction = newdb ? "upgrade" : action;
  const params = () => ({ container: container.name, action: rpcAction, database: database || undefined,
    update: newdb ? [] : csv(update), install: newdb ? ["base"] : csv(install), demo });

  useEffect(() => {
    const timer = setTimeout(() => {
      setError(null);
      rpc.request<Plan>("docker.plan", params()).then(setPlan).catch((e) => { setPlan(null); setError(String(e.message)); });
    }, 300);
    return () => clearTimeout(timer);
  }, [container.name, action, database, update, install, demo]); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    setError(null);
    try {
      job.begin((await rpc.request<{ run_id: string }>("docker.run", params())).run_id);
    } catch (e) {
      setError(String((e as Error).message));
    }
  };
  const meta = ACTION[action];
  return (
    <Dialog size="lg" icon={meta.icon} title={`${meta.title} ${container.name}`} subtitle={container.image} onClose={() => onClose(!!job.finished)} locked={job.busy}
      footer={<>
        <button className="btn" disabled={job.busy} onClick={() => onClose(!!job.finished)}>{job.started ? "Close" : "Cancel"}</button>
        {!job.started && <button className={`btn ${action === "stop" ? "danger" : "primary"}`} disabled={!plan?.ok} onClick={start}>{meta.icon}{plan?.ok ? meta.button : "Fix the failed checks first"}</button>}
      </>}>
      {error && <Callout tone="bad">{error}</Callout>}
      {!job.started && (
        <>
          {action === "stop" && <p className="muted">Odoo gets up to 30 seconds to shut down cleanly.</p>}
          {newdb && (
            <>
              <Field label="New database name"><input className="mono" value={database} onChange={(e) => setDatabase(e.target.value)} autoFocus /></Field>
              <CheckBox checked={!demo} onChange={(v) => setDemo(!v)}>No demo data</CheckBox>
              <p className="muted small">Odoo creates the database and installs the base module. This takes about a minute. Then open it in the browser from the port link.</p>
              {dbs.includes(database) && <Callout tone="warn">{database} exists already: use Upgrade… to change it.</Callout>}
            </>
          )}
          {action === "upgrade" && (
            <>
              <Field label="Database">
                <input list="docker-dbs" className="mono" value={database} onChange={(e) => setDatabase(e.target.value)} autoFocus />
              </Field>
              <datalist id="docker-dbs">{dbs.map((d) => <option key={d} value={d} />)}</datalist>
              <div className="grid2">
                <Field label={<>Modules to update <code className="dim">-u</code></>} hint="comma separated"><input className="mono" value={update} onChange={(e) => setUpdate(e.target.value)} /></Field>
                <Field label={<>Modules to install <code className="dim">-i</code></>} hint="comma separated"><input className="mono" value={install} onChange={(e) => setInstall(e.target.value)} /></Field>
              </div>
              <Callout tone="info">Snapshot the database first (Databases → container {container.name}) if the data matters.</Callout>
            </>
          )}
          {plan && <><Checks checks={plan.checks} /><Steps steps={plan.steps} /></>}
        </>
      )}
      {job.started && <JobView job={job} />}
    </Dialog>
  );
}

export function DockerLogsDialog({ container, onClose, onError }: { container: Container; onClose: () => void; onError: (m: string) => void }) {
  const [text, setText] = useState("");
  const [groups, setGroups] = useState<ProblemGroup[]>([]);
  const [view, setView] = useState<"log" | "problems">("log");
  const [follow, setFollow] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const r = await rpc.request<{ text: string; analysis: { groups: ProblemGroup[] } }>("docker.logs", { container: container.name, tail: 1000 });
      setText(r.text.slice(-400_000));
      setGroups(r.analysis.groups);
      setLoaded(true);
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

  return (
    <Dialog size="xl" icon={<ScrollText />} title={`Logs of ${container.name}`} subtitle="last 1000 lines" onClose={onClose}>
      <div className="row">
        <Segmented label="Log view" value={view} onChange={setView} options={[{ value: "log", label: "Log" }, { value: "problems", label: `Problems${groups.length ? ` (${groups.length})` : ""}` }]} />
        <span className="grow" />
        <CheckBox checked={follow} onChange={setFollow}>Follow (every 3 s)</CheckBox>
        <button className="btn sm" onClick={load} disabled={loading}><RefreshCw />{loading ? "Reading…" : "Refresh"}</button>
      </div>
      {!loaded ? <Loading>Reading logs…</Loading> : view === "log" ? (
        <div className="job-log" style={{ height: "calc(100vh - 260px)" }}><LogConsole text={text} empty="The container has written nothing." /></div>
      ) : (
        <div className="panel"><ProblemList groups={groups} /></div>
      )}
    </Dialog>
  );
}

export function DockerShellDialog({ container, onClose, onError }: { container: Container; onClose: () => void; onError: (m: string) => void }) {
  const [database, setDatabase] = useState("");
  const [command, setCommand] = useState<string | null>(null);
  const dbs = useDockerDbs(container);
  useEffect(() => {
    if (!database) { setCommand(null); return; }
    rpc.request<{ command: string }>("docker.shell", { container: container.name, database })
      .then((r) => setCommand(r.command)).catch((e) => { setCommand(null); onError(String(e.message)); });
  }, [database]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Dialog icon={<TerminalSquare />} title={`Shell in ${container.name}`} onClose={onClose} footer={<button className="btn" onClick={onClose}>Close</button>}>
      <p className="muted">odoo shell is interactive and needs a terminal. Pick a database, copy the command and run it in your terminal.</p>
      <Field label="Database">
        <input list="shell-dbs" className="mono" value={database} onChange={(e) => setDatabase(e.target.value)} autoFocus />
      </Field>
      <datalist id="shell-dbs">{dbs.map((d) => <option key={d} value={d} />)}</datalist>
      {command && <Cmd>{command}</Cmd>}
    </Dialog>
  );
}

export function NewStackDialog({ onClose }: { onClose: (changed: boolean) => void }) {
  const [listing, setListing] = useState<DockerListing | null>(null);
  const [name, setName] = useState("");
  const [version, setVersion] = useState("");
  const [port, setPort] = useState("");
  const [addons, setAddons] = useState("");
  const [plan, setPlan] = useState<NewPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const job = useJob("docker");
  const params = () => ({ name, version, port: port ? Number(port) : undefined, addons: addons || undefined });

  useEffect(() => {
    rpc.request<DockerListing>("docker.list").then((l) => {
      setListing(l);
      setVersion(l.versions.includes("18.0") ? "18.0" : l.versions[l.versions.length - 1] ?? "");
    }).catch((e) => setError(String(e.message)));
  }, []);
  useEffect(() => {
    if (!name) { setPlan(null); return; }
    const timer = setTimeout(() => {
      setError(null);
      rpc.request<NewPlan>("docker.new_plan", params()).then(setPlan).catch((e) => { setPlan(null); setError(String(e.message)); });
    }, 300);
    return () => clearTimeout(timer);
  }, [name, version, port, addons]); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    setError(null);
    try {
      job.begin((await rpc.request<{ run_id: string }>("docker.new_run", params())).run_id);
    } catch (e) {
      setError(String((e as Error).message));
    }
  };
  const url = plan?.port ? `http://localhost:${plan.port}` : "";
  return (
    <Dialog size="lg" icon={<ContainerIcon />} title="New Docker Odoo" subtitle="Odoo and PostgreSQL in containers" onClose={() => onClose(!!job.finished)} locked={job.busy}
      footer={<>
        <button className="btn" disabled={job.busy} onClick={() => onClose(!!job.finished)}>{job.started ? "Close" : "Cancel"}</button>
        {!job.started && <button className="btn primary" disabled={!plan?.ok} onClick={start}>{plan?.ok || !name ? "Create" : "Fix the failed checks first"}</button>}
      </>}>
      {error && <Callout tone="bad">{error}</Callout>}
      {listing && !listing.available && <Callout tone="warn">Docker is not available: {listing.error}</Callout>}
      {!job.started && listing && (
        <>
          <p className="muted">Creates Odoo and PostgreSQL as containers, with its own config and addons folders in <code>{listing.stacks_root}/&lt;name&gt;</code>. Nothing is installed on this machine.</p>
          <div className="grid2">
            <Field label="Name" hint="lowercase letters, digits, - and _"><input className="mono" value={name} onChange={(e) => setName(e.target.value.toLowerCase())} autoFocus /></Field>
            <Field label="Odoo version">
              <select value={version} onChange={(e) => setVersion(e.target.value)}>{listing.versions.map((v) => <option key={v} value={v}>{v}</option>)}</select>
            </Field>
            <Field label="Port on this machine" hint="empty: first free"><input className="mono" value={port} onChange={(e) => setPort(e.target.value.replace(/\D/g, ""))} /></Field>
            <Field label="Your addons folder" hint="absolute path; empty: a new empty folder"><input className="mono" value={addons} onChange={(e) => setAddons(e.target.value)} /></Field>
          </div>
          {plan && <><Checks checks={plan.checks} /><Steps steps={plan.steps} /></>}
        </>
      )}
      {!listing && !error && <Loading />}
      {job.started && (
        <JobView job={job} running="Working. The first run downloads the images and can take minutes." failed="Failed. What this run made was removed.">
          {job.finished?.ok && <Callout tone="ok">Odoo is starting at <b className="mono">{url}</b>. Then use <b>New database…</b> on the container to create the first database.</Callout>}
        </JobView>
      )}
    </Dialog>
  );
}

export function DeleteStackDialog({ container, onClose }: { container: Container; onClose: (changed: boolean) => void }) {
  const [confirm, setConfirm] = useState("");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const job = useJob("docker");
  const name = container.app_stack ?? "";

  useEffect(() => {
    const timer = setTimeout(() => {
      setError(null);
      rpc.request<Plan>("docker.delete_plan", { container: container.name, confirm: confirm || undefined })
        .then(setPlan).catch((e) => { setPlan(null); setError(String(e.message)); });
    }, 300);
    return () => clearTimeout(timer);
  }, [container.name, confirm]);

  const start = async () => {
    setError(null);
    try {
      job.begin((await rpc.request<{ run_id: string }>("docker.delete_run", { container: container.name, confirm })).run_id);
    } catch (e) {
      setError(String((e as Error).message));
    }
  };
  return (
    <Dialog size="lg" icon={<Trash2 />} title={`Delete stack ${name}`} onClose={() => onClose(!!job.finished)} locked={job.busy}
      footer={<>
        <button className="btn" disabled={job.busy} onClick={() => onClose(!!job.finished)}>{job.started ? "Close" : "Cancel"}</button>
        {!job.started && <button className="btn danger solid" disabled={!plan?.ok} onClick={start}><Trash2 />{plan?.ok ? "Delete stack" : "Type the name to confirm"}</button>}
      </>}>
      {error && <Callout tone="bad">{error}</Callout>}
      {!job.started && (
        <>
          <Callout tone="bad">
            Removes the Odoo and database containers, <b>all databases and filestores</b> of this stack, and its folder. This cannot be undone. Back up what you need first (Databases).
          </Callout>
          <Field label={<>Type <b className="mono" style={{ color: "var(--text)" }}>{name}</b> to confirm</>}>
            <input className="mono" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="off" autoFocus />
          </Field>
          {plan && <><Checks checks={plan.checks.filter((c) => c.id !== "confirm" || confirm)} /><Steps steps={plan.steps} /></>}
        </>
      )}
      {job.started && <JobView job={job} running="Removing…" />}
    </Dialog>
  );
}
