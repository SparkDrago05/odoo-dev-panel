import {
  Boxes, Copy, FileCode2, FolderGit2, Database, ExternalLink, FileCog, FolderTree, GitCompare, HeartPulse, LayoutGrid, Pencil, Play, RefreshCw, ShieldCheck, Square, Stethoscope, TerminalSquare,
} from "lucide-react";
import { useModuleCenter } from "../features/ModuleCenter";
import { PythonPanel } from "../features/PythonEnv";
import { RuntimeControls } from "../features/Run";
import { rpc } from "../rpc";
import { Inspector, InspectorSection } from "../shell/Chrome";
import { installLabel, shortVersion } from "../shell/Sidebar";
import { type InstallTab, sessionKey, useApp } from "../state/app";
import type { Instance } from "../types";
import { ActionMenu } from "../ui/Menu";
import { Tabs } from "../ui/Tabs";
import { ago, Badge, Callout, Dot, EmptyState, KV, Stat } from "../ui/primitives";
import { View, ViewHead, Panel } from "./common";
import { EmbeddedDatabases } from "./Databases";
import { useRepoWorkspace } from "./Repositories";
import { FindingList } from "./Doctor";

const TABS: { id: InstallTab; label: string; icon: React.ReactNode }[] = [
  { id: "overview", label: "Overview", icon: <LayoutGrid /> },
  { id: "instances", label: "Instances", icon: <FileCog /> },
  { id: "repos", label: "Repositories", icon: <FolderGit2 /> },
  { id: "databases", label: "Databases", icon: <Database /> },
  { id: "modules", label: "Modules", icon: <Boxes /> },
  { id: "python", label: "Python", icon: <FileCode2 /> },
  { id: "doctor", label: "Diagnostics", icon: <HeartPulse /> },
];

function InstanceCard({ inst }: { inst: Instance }) {
  const app = useApp();
  const st = app.instanceState(inst.path);
  const db = inst.options?.db_name && inst.options.db_name !== "False" ? inst.options.db_name : null;
  return (
    <div className="instance-card" role="button" tabIndex={0} onClick={() => app.nav({ view: "instance", path: inst.path })}
      onKeyDown={(e) => { if (e.key === "Enter") app.nav({ view: "instance", path: inst.path }); }}>
      <div className="top">
        <Dot tone={st.running ? "ok" : inst.problems.length ? "warn" : "idle"} live={st.running && !st.external} />
        <span className="name">{inst.name}</span>
        {st.running && st.port && <Badge mono tone="ok">:{st.port}</Badge>}
        <InstanceMenu inst={inst} />
      </div>
      <span className="path" title={inst.path}>{inst.path}</span>
      <div className="row tight">
        {st.running ? <Badge tone="ok">{st.external ? (st.unit ? `running · ${st.unit}` : "running outside the app") : "running"}</Badge> : <Badge>stopped</Badge>}
        {db && <Badge mono>{db}</Badge>}
        {inst.options?.http_port && <Badge mono>port {inst.options.http_port}</Badge>}
        {inst.problems.length > 0 && <Badge tone="warn" title={inst.problems.join("; ")}>{inst.problems.length} problem{inst.problems.length > 1 ? "s" : ""}</Badge>}
      </div>
    </div>
  );
}

export function InstanceMenu({ inst }: { inst: Instance }) {
  const app = useApp();
  const st = app.instanceState(inst.path);
  return (
    <ActionMenu items={[
      st.running && st.session ? { label: "Stop", icon: <Square />, onSelect: () => app.stopSession(st.session!) }
        : { label: "Start", icon: <Play />, onSelect: () => app.quickStart(inst.path), disabled: st.running },
      { label: "Run with options…", icon: <Play />, onSelect: () => app.setDialog({ kind: "run", instance: inst.path }) },
      st.running && st.port ? { label: "Open in browser", icon: <ExternalLink />, onSelect: () => app.openPort(st.port!) } : null,
      "sep",
      { label: "Edit config", icon: <Pencil />, onSelect: () => app.setDialog({ kind: "config", path: inst.path, root: inst.installation }) },
      { label: "Modules", icon: <Boxes />, onSelect: () => app.setDialog({ kind: "modules", path: inst.path }) },
      { label: "Compare…", icon: <GitCompare />, onSelect: () => app.setDialog({ kind: "compare", path: inst.path }) },
      "sep",
      { label: "Copy config path", icon: <Copy />, onSelect: () => navigator.clipboard.writeText(inst.path) },
    ]} />
  );
}

export function InstallationView({ root, tab = "overview", repo, module }: { root: string; tab?: InstallTab; repo?: string; module?: string }) {
  const app = useApp();
  const { snap, doctor } = app;
  const inst = snap?.installations.find((i) => i.root === root);
  const configs = snap?.instances.filter((i) => i.installation === root) ?? [];
  const modCenter = useModuleCenter({ root, selected: module, enabled: tab === "modules",
    onSelect: (m) => app.nav({ view: "installation", root, tab: "modules", module: m }) });
  const repoWs = useRepoWorkspace({ root, selected: repo, enabled: tab === "repos",
    onSelect: (p) => app.nav({ view: "installation", root, tab: "repos", repo: p }) });

  if (!snap) return <View><EmptyState icon={<FolderTree />} title="Scanning…" /></View>;
  if (!inst) {
    return (
      <View>
        <EmptyState icon={<FolderTree />} title="Installation not found" actions={<button className="btn" onClick={() => app.nav({ view: "home" })}>Back to overview</button>}>
          {root} was not found by the last scan. It may have been moved or removed.
        </EmptyState>
      </View>
    );
  }
  const dbs = snap.databases.find((d) => d.installation === root);
  const running = configs.filter((c) => app.instanceState(c.path).running);
  const units = snap.units.filter((u) => configs.some((c) => c.path === u.config));
  const findings = doctor.report?.findings.filter((f) => f.installation === root) ?? [];
  const setTab = (t: InstallTab) => app.nav({ view: "installation", root, tab: t });

  return (
    <>
    <View>
      <ViewHead icon={<span className="mono strong" style={{ fontSize: 13 }}>{shortVersion(inst.version)}</span>}
        title={<>Odoo {inst.version ?? "?"} <span className="dim" style={{ fontWeight: 500 }}>· {installLabel(inst)}</span></>}
        subtitle={<>
          <span className="mono small">{inst.root}</span>
          <Badge tone={inst.venv_ok === false ? "bad" : inst.venv_ok ? "ok" : undefined}>{inst.venv_ok === false ? "venv broken" : inst.venv_ok ? `Python ${inst.python_version ?? ""}` : "no venv"}</Badge>
          <Badge mono>runs as {inst.owner ?? "?"}</Badge>
          {inst.adopted && <Badge tone="accent">adopted</Badge>}
        </>}
        actions={<>
          <button className="btn" onClick={() => app.setDialog({ kind: "run", instance: configs[0]?.path })} disabled={!configs.length}><Play />Run…</button>
          <ActionMenu label="Installation actions" items={[
            inst.venv_ok === false && { label: "Repair venv…", icon: <Stethoscope />, onSelect: () => app.setDialog({ kind: "repair-venv", root }) },
            { label: "Fix config permissions…", icon: <ShieldCheck />, onSelect: () => app.setDialog({ kind: "fix-perms", root }) },
            { label: "Export as a profile…", icon: <Copy />, onSelect: () => app.setDialog({ kind: "profile-export", root }) },
            { label: "Apply a profile's repositories…", icon: <FolderGit2 />, onSelect: () => app.setDialog({ kind: "profile-apply", installation: root }) },
            { label: "Compare with another installation…", icon: <GitCompare />, onSelect: () => app.setDialog({ kind: "compare", mode: "installations", path: configs[0]?.path }) },
            "sep",
            { label: inst.adopted ? "Release (forget adoption)" : "Adopt into the registry", onSelect: () => app.act("updating the registry", async () => { await rpc.request("discover.adopt", { root, adopt: !inst.adopted }); await app.scan(); }) },
            { label: "Scan again", icon: <RefreshCw />, onSelect: () => app.scan() },
            { label: "Copy path", icon: <Copy />, onSelect: () => navigator.clipboard.writeText(root) },
          ]} />
        </>} />

      {inst.venv_ok === false && (
        <Callout tone="bad" title="The venv is broken" action={<button className="btn sm primary" onClick={() => app.setDialog({ kind: "repair-venv", root })}><Stethoscope />Repair…</button>}>
          {inst.venv_problem ?? "Odoo cannot start with it."} The repair builds a new venv next to the old one and swaps only after it validates.
        </Callout>
      )}

      <Tabs label="Installation" value={tab} onChange={setTab}
        tabs={TABS.map((t) => ({ ...t, count: t.id === "instances" ? <span className="count-pill">{configs.length}</span> : t.id === "doctor" && findings.length ? <span className={`count-pill ${findings.some((f) => f.severity === "error") ? "bad" : "warn"}`}>{findings.length}</span> : undefined }))} />

      {tab === "overview" && (
        <>
          <div className="grid3">
            <Stat label="Instances" icon={<FileCog />} value={`${running.length} / ${configs.length}`} sub="running / configs" />
            <Stat label="Databases" icon={<Database />} value={dbs?.error ? "locked" : dbs?.databases.length ?? 0} sub={dbs?.error ? "unlock the agent to list them" : `role ${inst.pg_role ?? "?"}`} />
            <Stat label="Health" icon={<HeartPulse />} value={doctor.report ? (findings.length ? `${findings.length} findings` : "Healthy") : inst.venv_ok === false ? "Broken venv" : "—"}
              sub={doctor.report ? `checked ${ago(doctor.at)}` : <a onClick={() => { setTab("doctor"); app.runDoctor(); }}>Run checks</a>} />
          </div>
          <Panel title="Instances" actions={<button className="btn ghost sm" onClick={() => setTab("instances")}>All instances</button>}>
            {configs.length === 0 ? <p className="muted">No config files found for this installation.</p> : (
              <div className="grid2">{configs.slice(0, 6).map((c) => <InstanceCard key={c.path} inst={c} />)}</div>
            )}
          </Panel>
          <Panel title="Technical details">
            <KV items={[
              ["Root", <code>{inst.root}</code>],
              ["Odoo source", <code>{inst.source}</code>],
              ["venv", inst.venv ? <code>{inst.venv}</code> : null],
              ["Python", inst.python_version ? `${inst.python_version}${inst.venv_built_for && inst.venv_built_for !== inst.python_version ? ` (built for ${inst.venv_built_for})` : ""}` : null],
              ["Run-as user", inst.owner, "mono"],
              ["PostgreSQL role", inst.pg_role, "mono"],
              ["Home", inst.home ? <code>{inst.home}</code> : null],
              ["systemd units", units.length ? units.map((u) => `${u.name} (${u.active_state})`).join(", ") : "none"],
            ]} />
          </Panel>
        </>
      )}

      {tab === "instances" && (
        configs.length === 0 ? <EmptyState icon={<FileCog />} title="No configs">No config file points at this installation.</EmptyState> : (
          <div className="grid2">{configs.map((c) => <InstanceCard key={c.path} inst={c} />)}</div>
        )
      )}

      {tab === "repos" && repoWs.body}

      {tab === "databases" && <EmbeddedDatabases root={root} />}

      {tab === "modules" && modCenter.body}

      {tab === "python" && <PythonPanel root={root} />}

      {tab === "doctor" && (
        <Panel title="Diagnostics" actions={<button className="btn sm" onClick={app.runDoctor} disabled={doctor.running}><RefreshCw />{doctor.running ? "Checking…" : doctor.report ? "Check again" : "Run checks"}</button>} flush>
          {!doctor.report ? (
            <EmptyState icon={<Stethoscope />} title={doctor.running ? "Checking…" : "Not checked yet"}>The Doctor is read-only: nothing changes unless you press Repair.</EmptyState>
          ) : findings.length === 0 ? (
            <EmptyState icon={<HeartPulse />} title="No problems found">Nothing the Doctor checks is wrong with this installation.</EmptyState>
          ) : <FindingList findings={findings} onOpen={(f) => app.nav({ view: "doctor", finding: `${f.code}:${f.subject}` })} />}
        </Panel>
      )}
    </View>
    {tab === "repos" && repoWs.inspector}
    {tab === "modules" && modCenter.inspector}
    </>
  );
}

export function InstanceView({ path }: { path: string }) {
  const app = useApp();
  const { snap, sessions } = app;
  const inst = snap?.instances.find((i) => i.path === path);
  if (!snap) return <View><EmptyState icon={<FileCog />} title="Scanning…" /></View>;
  if (!inst) {
    return <View><EmptyState icon={<FileCog />} title="Instance not found" actions={<button className="btn" onClick={() => app.nav({ view: "home" })}>Back to overview</button>}>{path} was not found by the last scan.</EmptyState></View>;
  }
  const installation = snap.installations.find((i) => i.root === inst.installation);
  const st = app.instanceState(path);
  const history = sessions.filter((s) => s.meta?.instance === path).slice(0, 8);
  const units = snap.units.filter((u) => u.config === path);
  const opt = (k: string) => { const v = inst.options?.[k]; return v && v !== "False" ? v : null; };
  const addons = (opt("addons_path") ?? "").split(",").map((p) => p.trim()).filter(Boolean);

  return (
    <>
      <View>
        <ViewHead icon={<FileCog />}
          title={<>{inst.name}<Dot tone={st.running ? "ok" : "idle"} live={st.running && !st.external} /></>}
          subtitle={<>
            <span className="mono small">{inst.path}</span>
            {installation && <Badge>Odoo {installation.version}</Badge>}
            {st.running && <Badge tone="ok">{st.external ? "running outside the app" : `running${st.port ? ` :${st.port}` : ""}`}</Badge>}
          </>}
          actions={<>
            {st.running && st.port && <button className="btn" onClick={() => app.openPort(st.port!)}><ExternalLink />Open</button>}
            {st.running && st.session ? <button className="btn" onClick={() => app.stopSession(st.session!)}><Square />Stop</button>
              : <button className="btn primary" disabled={st.running || !installation} onClick={() => app.quickStart(path)} title={st.running ? "Already running" : "Start with the config's defaults"}><Play />Start</button>}
            <button className="btn" onClick={() => app.setDialog({ kind: "config", path, root: inst.installation })}><Pencil />Edit config</button>
            <InstanceMenu inst={inst} />
          </>} />

        {inst.problems.map((p) => <Callout key={p} tone="warn">{p}</Callout>)}
        {st.external && <Callout tone="info">This instance runs outside Odoo Dev Panel (pid {st.pid}{st.unit ? `, systemd unit ${st.unit}` : ""}). Its output is not available here{st.unit ? "; see Services for its journal" : ""}.</Callout>}

        <Panel title={<h2>Run with options</h2>}>
          <RuntimeControls fixed={path} />
        </Panel>

        <Panel title="Recent sessions" flush actions={<button className="btn ghost sm" onClick={() => app.nav({ view: "sessions" })}>All sessions</button>}>
          {history.length === 0 ? <p className="muted" style={{ padding: 14 }}>Not started from Odoo Dev Panel yet.</p> : (
            <div className="list">
              {history.map((s) => (
                <div key={sessionKey(s)} className="list-row clickable" onClick={() => { app.follow(s); app.nav({ view: "sessions", key: sessionKey(s) }); }}>
                  <Dot tone={s.state === "running" ? "ok" : s.exit_code ? "bad" : "idle"} live={s.state === "running"} />
                  <span className="grow truncate">{s.name}</span>
                  {s.meta?.kind && <Badge>{s.meta.kind}</Badge>}
                  {s.meta?.db && <Badge mono>{s.meta.db}</Badge>}
                  <span className="meta xs mono nowrap">{s.state === "running" ? "running" : `exit ${s.exit_code ?? "?"}`} · {ago(s.started_at)}</span>
                  <TerminalSquare style={{ width: 14, height: 14, color: "var(--text-3)" }} />
                </div>
              ))}
            </div>
          )}
        </Panel>
      </View>
      <Inspector title="Instance">
        <InspectorSection title="Config">
          <KV items={[
            ["HTTP port", opt("http_port"), "mono"],
            ["Database", opt("db_name") ?? <span className="dim">choose in browser</span>, "mono"],
            ["DB user", opt("db_user"), "mono"],
            ["DB host", opt("db_host") ?? <span className="dim">Unix socket</span>, "mono"],
          ]} />
        </InspectorSection>
        <InspectorSection title={`addons_path (${addons.length})`}>
          {addons.length ? <div className="stack tight">{addons.map((a) => <code key={a} className="xs break">{a}</code>)}</div> : <span className="muted small">Odoo's own addons only</span>}
        </InspectorSection>
        <InspectorSection title="Installation">
          <KV items={[
            ["Root", installation ? <a onClick={() => app.nav({ view: "installation", root: installation.root })}>{installation.root}</a> : null],
            ["Runs as", installation?.owner, "mono"],
            ["Python", installation?.python_version],
          ]} />
        </InspectorSection>
        {(st.running || units.length > 0) && (
          <InspectorSection title="Runtime">
            <KV items={[
              ["State", st.running ? "running" : "stopped"],
              ["PID", st.pid, "mono"],
              ["Port", st.port, "mono"],
              ["systemd", units.length ? units.map((u) => <a key={u.name} onClick={() => app.nav({ view: "services", name: u.name })}>{u.name}</a>) : null],
            ]} />
          </InspectorSection>
        )}
        <div className="action-stack">
          <button className="btn" onClick={() => app.setDialog({ kind: "modules", path })}><Boxes />Modules</button>
          <button className="btn" onClick={() => app.setDialog({ kind: "compare", path })}><GitCompare />Compare with…</button>
          {inst.installation && <button className="btn" onClick={() => app.nav({ view: "databases", root: inst.installation! })}><Database />Databases</button>}
        </div>
      </Inspector>
    </>
  );
}
