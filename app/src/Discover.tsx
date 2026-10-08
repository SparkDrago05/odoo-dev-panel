import { Fragment, useCallback, useEffect, useState } from "react";
import { Compare } from "./Compare";
import { ConfigEditor } from "./ConfigEditor";
import Databases from "./Databases";
import Doctor from "./Doctor";
import { Modules } from "./Modules";
import { rpc } from "./rpc";

type Installation = {
  root: string; source: string; version: string | null; owner: string | null; venv_ok: boolean | null;
  pg_role: string | null; adopted: boolean; name: string;
};
type Instance = { path: string; name: string; installation: string | null; problems: string[]; version_hint: string | null };
type Database = { name: string; size: number; filestore: string | null; filestore_exists: boolean | null };
type DbEntry = { installation: string; databases: Database[]; error: string | null };
type Proc = { pid: number; user: string | null; port: number | null; instance: string | null; installation: string | null; database: string | null };
type Unit = { name: string; active_state: string | null; sub_state: string | null; user: string | null; config: string | null };
type Snapshot = {
  installations: Installation[]; instances: Instance[]; databases: DbEntry[]; processes: Proc[]; units: Unit[];
  ports: { conflicts: { port: number; kind: string; pids: number[]; holder: number | null }[] };
  missing: { root: string; name: string }[];
  unreadable: string[];
  registry_error?: string;
};

type Tab = "overview" | "configs" | "databases" | "doctor";
const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "configs", label: "Configs" },
  { id: "databases", label: "Databases" },
  { id: "doctor", label: "Doctor" },
];

const mb = (n: number) => `${(n / 1048576).toFixed(n < 10485760 ? 1 : 0)} MB`;
const venvLabel = (ok: boolean | null) => (ok === null ? "no venv" : ok ? "venv ok" : "venv broken");

export default function Discover({ onError }: { onError: (message: string) => void }) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [scanning, setScanning] = useState(false);
  const [workspace, setWorkspace] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [editing, setEditing] = useState<{ path: string; root: string | null } | null>(null);
  const [compareOf, setCompareOf] = useState<string | null>(null);
  const [modulesOf, setModulesOf] = useState<string | null>(null);

  const scan = useCallback(async () => {
    setScanning(true);
    try {
      setSnap(await rpc.request<Snapshot>("discover.scan"));
    } catch (e) {
      onError(String((e as Error).message));
    } finally {
      setScanning(false);
    }
  }, [onError]);

  useEffect(() => {
    scan();
  }, [scan]);

  const adopt = async (inst: Installation) => {
    try {
      await rpc.request("discover.adopt", { root: inst.root, adopt: !inst.adopted });
      await scan();
    } catch (e) {
      onError(String((e as Error).message));
    }
  };

  const current = snap?.installations.find((i) => i.root === workspace) ?? null;
  const orphans = snap?.instances.filter((i) => i.installation === null) ?? [];
  const broken = snap?.installations.filter((i) => i.venv_ok === false).length ?? 0;

  return (
    <section>
      {!current && <h2>Installations</h2>}
      <div className="row">
        <button onClick={scan} disabled={scanning}>{scanning ? "Scanning…" : "Scan"}</button>
        {snap && (
          <span className="muted">
            {snap.installations.length} installations{broken ? `, ${broken} with a broken venv` : ""}, {snap.instances.length} configs,{" "}
            {snap.processes.length} running
          </span>
        )}
      </div>
      {snap?.registry_error && <p className="muted">Registry problem: {snap.registry_error}</p>}
      {snap && snap.ports.conflicts.length > 0 && (
        <p className="muted">
          Port conflicts: {snap.ports.conflicts.map((c) => `${c.port} (${c.kind}, pid ${c.pids.join("/")})`).join(", ")}
        </p>
      )}
      {snap && snap.installations.length === 0 && (
        <div className="empty-state">
          <h3>No Odoo installation found</h3>
          <p className="muted">Odoo Dev Panel looks in /opt, /srv and your home folder. Create a new installation, or scan again after you add one.</p>
        </div>
      )}
      {snap && !current && snap.installations.length > 0 && (
        <table>
          <thead>
            <tr><th>Path</th><th>Odoo</th><th>Run as</th><th>State</th><th>Configs</th><th>Databases</th><th /></tr>
          </thead>
          <tbody>
            {snap.installations.map((inst) => {
              const configs = snap.instances.filter((i) => i.installation === inst.root);
              const dbs = snap.databases.find((d) => d.installation === inst.root);
              const running = snap.processes.filter((p) => p.installation === inst.root);
              return (
                <tr key={inst.root} className="clickable" tabIndex={0}
                  onClick={() => { setWorkspace(inst.root); setTab("overview"); }}
                  onKeyDown={(e) => { if (e.key === "Enter") { setWorkspace(inst.root); setTab("overview"); } }}>
                  <td>{inst.root}{inst.adopted && <span className="muted"> · adopted as {inst.name}</span>}</td>
                  <td>{inst.version ?? "?"}</td>
                  <td>{inst.owner ?? "?"}</td>
                  <td>
                    <span className={`dot ${inst.venv_ok === false ? "error" : "running"}`} /> {venvLabel(inst.venv_ok)}
                    {running.length > 0 && <span className="muted"> · {running.map((p) => `:${p.port}`).join(" ")} running</span>}
                  </td>
                  <td>{configs.length}</td>
                  <td>{dbs?.error ? <span className="muted">unavailable</span> : dbs?.databases.length ?? 0}</td>
                  <td className="actions">
                    <button onClick={(e) => { e.stopPropagation(); adopt(inst); }}>{inst.adopted ? "Release" : "Adopt"}</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {snap && current && (() => {
        const configs = snap.instances.filter((i) => i.installation === current.root);
        const dbs = snap.databases.find((d) => d.installation === current.root);
        const running = snap.processes.filter((p) => p.installation === current.root);
        const units = snap.units.filter((u) => configs.some((c) => u.config === c.path));
        return (
          <div className="workspace">
            <div className="row between">
              <div>
                <button onClick={() => setWorkspace(null)}>← Installations</button>
              </div>
              <button onClick={() => adopt(current)}>{current.adopted ? "Release" : "Adopt"}</button>
            </div>
            <h2>Odoo {current.version ?? "?"} <span className="muted">· {current.root}</span></h2>
            <div className="tabs" role="tablist">
              {TABS.map((t) => (
                <button key={t.id} role="tab" aria-selected={tab === t.id} className={`tab ${tab === t.id ? "active" : ""}`} onClick={() => setTab(t.id)}>{t.label}</button>
              ))}
            </div>
            {tab === "overview" && (
              <div className="grid2">
                <div className="card"><div className="muted">Health</div><div><span className={`dot ${current.venv_ok === false ? "error" : "running"}`} /> {venvLabel(current.venv_ok)}</div></div>
                <div className="card"><div className="muted">Run as</div><div>{current.owner ?? "?"}</div></div>
                <div className="card"><div className="muted">PostgreSQL role</div><div>{current.pg_role ?? "-"}</div></div>
                <div className="card"><div className="muted">Configs / databases</div><div>{configs.length} / {dbs?.error ? "unavailable" : dbs?.databases.length ?? 0}</div></div>
                <div className="card"><div className="muted">Running</div><div>{running.length ? running.map((p) => `:${p.port ?? "?"} (pid ${p.pid})`).join(", ") : "nothing"}</div></div>
                <div className="card"><div className="muted">systemd units</div><div>{units.length ? units.map((u) => `${u.name} ${u.active_state}`).join(", ") : "none"}</div></div>
              </div>
            )}
            {tab === "configs" && (
              configs.length === 0 ? <p className="muted">No config files found for this installation.</p> : (
                <table>
                  <tbody>
                    {configs.map((c) => (
                      <tr key={c.path}>
                        <td>{c.name}<div className="muted">{c.path}{c.problems.length ? ` · ${c.problems.join("; ")}` : ""}</div></td>
                        <td className="actions">
                          <button onClick={() => setModulesOf(c.path)}>Modules</button>{" "}
                          <button disabled={snap.instances.length < 2} onClick={() => setCompareOf(c.path)}>Compare</button>{" "}
                          <button onClick={() => setEditing({ path: c.path, root: current.root })}>Edit</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )
            )}
            {tab === "databases" && <Databases key={current.root} onError={onError} fixedRoot={current.root} />}
            {tab === "doctor" && <Doctor key={current.root} onError={onError} installation={current.root} />}
          </div>
        );
      })()}
      {orphans.length > 0 && (
        <p className="muted">
          Orphan configs (no installation):{" "}
          {orphans.map((o) => (
            <a key={o.path} href="#" onClick={(e) => { e.preventDefault(); setEditing({ path: o.path, root: null }); }}>
              {o.path}{o.version_hint ? ` [${o.version_hint}]` : ""}{" "}
            </a>
          ))}
        </p>
      )}
      {compareOf && snap && (
        <Compare path={compareOf} configs={snap.instances} installations={snap.installations} databases={snap.databases}
          onClose={() => setCompareOf(null)} />
      )}
      {modulesOf && <Modules path={modulesOf} onClose={() => setModulesOf(null)} />}
      {editing && <ConfigEditor path={editing.path} root={editing.root} onClose={(changed) => { setEditing(null); if (changed) scan(); }} />}
      {snap && snap.missing.length > 0 && <p className="muted">Adopted but not found: {snap.missing.map((m) => m.root).join(", ")}</p>}
      {snap && snap.unreadable.length > 0 && <p className="muted">Could not read (permission): {snap.unreadable.join(", ")}</p>}
      {snap && snap.units.length > 0 && (
        <p className="muted">Units: {snap.units.map((u) => `${u.name} ${u.active_state}/${u.sub_state}`).join(", ")}</p>
      )}
    </section>
  );
}
