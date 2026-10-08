import {
  Activity, ArrowRight, Container, ExternalLink, FileWarning, FolderTree, HeartPulse, Lock, PackagePlus, Play, Square, Stethoscope,
} from "lucide-react";
import { rpc } from "../rpc";
import { installLabel, installTone, shortVersion } from "../shell/Sidebar";
import { sessionKey, useApp } from "../state/app";
import { ActionMenu } from "../ui/Menu";
import { Badge, Callout, Dot, duration, EmptyState, Loading, Stat } from "../ui/primitives";
import { Panel, View, ViewHead } from "./common";

/** Start page: what runs now, what is broken, every installation at a glance. */
export function HomeView() {
  const app = useApp();
  const { snap, sessions, agents, doctor } = app;
  const running = sessions.filter((s) => s.state === "running");
  const locked = agents.filter((a) => a.state !== "running");
  const broken = snap?.installations.filter((i) => i.venv_ok === false) ?? [];
  const orphans = snap?.instances.filter((i) => !i.installation) ?? [];

  return (
    <View>
      <ViewHead title="Overview" subtitle={snap ? `${snap.installations.length} installations · ${snap.instances.length} configs · ${running.length} running` : "Scanning this machine…"}
        actions={<>
          <button className="btn" onClick={() => app.setDialog({ kind: "run" })}><Play />Run Odoo…</button>
          <button className="btn primary" onClick={() => app.setDialog({ kind: "provision" })}><PackagePlus />New installation</button>
        </>} />

      <div className="grid3">
        <Stat label="Running" icon={<Activity />} value={<>{running.length}{running.length > 0 && <Dot tone="ok" live />}</>} sub={running.length ? running.map((s) => s.name).join(", ") : "nothing started"} />
        <Stat label="Agents unlocked" icon={<Lock />} value={`${agents.length - locked.length} / ${agents.length}`} sub={locked.length ? `locked: ${locked.map((a) => a.user).join(", ")}` : "all unlocked"} />
        <Stat label="Health" icon={<HeartPulse />}
          value={doctor.report ? (doctor.report.counts.error ? <span style={{ color: "var(--danger)" }}>{doctor.report.counts.error} errors</span> : "No errors") : broken.length ? <span style={{ color: "var(--danger)" }}>{broken.length} broken venv</span> : "—"}
          sub={doctor.report ? `${doctor.report.counts.warning ?? 0} warnings · Doctor` : <a onClick={() => { app.nav({ view: "doctor" }); app.runDoctor(); }}>Run the Doctor</a>} />
      </div>

      {broken.map((i) => (
        <Callout key={i.root} tone="bad" title={`The venv of ${i.root} is broken`}
          action={<button className="btn sm" onClick={() => app.setDialog({ kind: "repair-venv", root: i.root })}><Stethoscope />Repair…</button>}>
          {i.venv_problem ?? "Odoo cannot start with it."}
        </Callout>
      ))}

      {running.length > 0 && (
        <Panel title="Running now" flush>
          <div className="list">
            {running.map((s) => (
              <div key={sessionKey(s)} className="list-row clickable" onClick={() => { app.follow(s); app.nav({ view: "sessions", key: sessionKey(s) }); }}>
                <Dot tone="ok" live />
                <div className="grow" style={{ display: "grid" }}>
                  <span className="strong truncate">{s.name}</span>
                  <span className="meta mono xs truncate">{s.user} · pid {s.pid} · {duration(s.started_at)}{s.meta?.db ? ` · ${s.meta.db}` : ""}</span>
                </div>
                {s.meta?.port && <Badge mono>:{s.meta.port}</Badge>}
                <div className="row-actions">
                  {s.meta?.port && <button className="btn sm" onClick={(e) => { e.stopPropagation(); app.openPort(s.meta!.port!); }}><ExternalLink />Open</button>}
                  <button className="btn sm" onClick={(e) => { e.stopPropagation(); app.stopSession(s); }}><Square />Stop</button>
                </div>
              </div>
            ))}
          </div>
        </Panel>
      )}

      <Panel title="Installations" actions={<button className="btn ghost sm" onClick={() => app.scan()} disabled={app.scanning}>{app.scanning ? "Scanning…" : "Scan again"}</button>} flush>
        {!snap ? <Loading>Looking in /opt, /srv and your home folder…</Loading> : snap.installations.length === 0 ? (
          <EmptyState icon={<FolderTree />} title="No Odoo installation found"
            actions={<><button className="btn primary" onClick={() => app.setDialog({ kind: "provision" })}><PackagePlus />New installation</button>
              <button className="btn" onClick={() => app.setDialog({ kind: "docker-new" })}><Container />New Docker Odoo</button></>}>
            Odoo Dev Panel looks in /opt, /srv and your home folder. Create one, run Odoo in Docker, or scan again after you add one.
          </EmptyState>
        ) : (
          <div className="list">
            {snap.installations.map((i) => {
              const configs = snap.instances.filter((x) => x.installation === i.root);
              const live = configs.filter((c) => app.instanceState(c.path).running);
              const dbs = snap.databases.find((d) => d.installation === i.root);
              return (
                <div key={i.root} className="list-row clickable" onClick={() => app.nav({ view: "installation", root: i.root })}>
                  <span className="ver mono badge" style={{ minWidth: 34, justifyContent: "center" }}>{shortVersion(i.version)}</span>
                  <div className="grow" style={{ display: "grid" }}>
                    <span className="strong truncate">{installLabel(i)}{i.adopted && <span className="dim small"> · adopted</span>}</span>
                    <span className="meta mono xs truncate">{i.root} · runs as {i.owner ?? "?"}</span>
                  </div>
                  <span className="meta small nowrap">{configs.length} config{configs.length === 1 ? "" : "s"} · {dbs?.error ? "databases locked" : `${dbs?.databases.length ?? 0} db`}</span>
                  {live.length > 0 && <Badge tone="ok">{live.length} running</Badge>}
                  <Badge tone={installTone(i) === "bad" ? "bad" : installTone(i) === "ok" ? "ok" : undefined}>{i.venv_ok === false ? "venv broken" : i.venv_ok === null ? "no venv" : `Python ${i.python_version ?? "ok"}`}</Badge>
                  <ActionMenu items={[
                    { label: "Open", icon: <ArrowRight />, onSelect: () => app.nav({ view: "installation", root: i.root }) },
                    { label: "Databases", icon: <FolderTree />, onSelect: () => app.nav({ view: "databases", root: i.root }) },
                    "sep",
                    { label: i.adopted ? "Release (forget adoption)" : "Adopt", onSelect: () => app.act("updating the registry", async () => { await rpc.request("discover.adopt", { root: i.root, adopt: !i.adopted }); await app.scan(); }) },
                  ]} />
                </div>
              );
            })}
          </div>
        )}
      </Panel>

      {snap && (orphans.length > 0 || snap.unreadable.length > 0 || snap.missing.length > 0 || snap.ports.conflicts.length > 0 || snap.registry_error) && (
        <Panel title="Discovery notes">
          {snap.registry_error && <Callout tone="warn" title="Registry problem">{snap.registry_error}</Callout>}
          {snap.ports.conflicts.length > 0 && (
            <Callout tone="warn" title="Port conflicts">
              <span className="mono small">{snap.ports.conflicts.map((c) => `${c.port} (${c.kind}, pid ${c.pids.join("/")})`).join(", ")}</span>
            </Callout>
          )}
          {orphans.length > 0 && (
            <Callout tone="info" title={`Configs without an installation (${orphans.length})`}>
              <span className="row tight" style={{ marginTop: 4 }}>
                {orphans.map((o) => (
                  <button key={o.path} className="chip" onClick={() => app.setDialog({ kind: "config", path: o.path, root: null })} title="Edit">
                    <FileWarning style={{ width: 12, height: 12 }} /><span className="mono">{o.path}</span>{o.version_hint && <span className="dim">{o.version_hint}</span>}
                  </button>
                ))}
              </span>
            </Callout>
          )}
          {snap.missing.length > 0 && <Callout tone="warn" title="Adopted but not found"><span className="mono small">{snap.missing.map((m) => m.root).join(", ")}</span></Callout>}
          {snap.unreadable.length > 0 && (
            <Callout tone="info" title="Could not read (permission)"><span className="mono small">{snap.unreadable.join(", ")}</span></Callout>
          )}
        </Panel>
      )}

      {locked.length > 0 && agents.length > 0 && (
        <Callout tone="info" title="Some agents are locked" action={<button className="btn sm" onClick={() => app.nav({ view: "sessions" })}>Agents<ArrowRight /></button>}>
          Odoo runs as its version user through an agent. A locked agent asks for your sudo password once per boot.
        </Callout>
      )}
      {doctor.report === null && !doctor.running && broken.length === 0 && snap && snap.installations.length > 0 && (
        <Callout tone="info" action={<button className="btn sm" onClick={() => { app.nav({ view: "doctor" }); app.runDoctor(); }}><Stethoscope />Run checks</button>}>
          The Doctor checks venvs, configs, addons paths, git, ports, units and filestores. It changes nothing.
        </Callout>
      )}
      {doctor.running && <Callout tone="info">Doctor is checking this machine…</Callout>}
    </View>
  );
}
