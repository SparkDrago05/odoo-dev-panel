import { Fragment, useCallback, useEffect, useState } from "react";
import { ConfigEditor } from "./ConfigEditor";
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
type Unit = { name: string; active_state: string | null; sub_state: string | null; user: string | null };
type Snapshot = {
  installations: Installation[]; instances: Instance[]; databases: DbEntry[]; processes: Proc[]; units: Unit[];
  ports: { conflicts: { port: number; kind: string; pids: number[]; holder: number | null }[] };
  missing: { root: string; name: string }[];
  unreadable: string[];
  registry_error?: string;
};

const mb = (n: number) => `${(n / 1048576).toFixed(n < 10485760 ? 1 : 0)} MB`;
const venvLabel = (ok: boolean | null) => (ok === null ? "no venv" : ok ? "venv ok" : "venv broken");

export default function Discover({ onError }: { onError: (message: string) => void }) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [scanning, setScanning] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ path: string; root: string | null } | null>(null);
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

  const orphans = snap?.instances.filter((i) => i.installation === null) ?? [];
  const broken = snap?.installations.filter((i) => i.venv_ok === false).length ?? 0;

  return (
    <section>
      <h2>Installations</h2>
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
      {snap && (
        <table>
          <thead>
            <tr><th>Path</th><th>Odoo</th><th>Run as</th><th>State</th><th>Configs</th><th>Databases</th><th /></tr>
          </thead>
          <tbody>
            {snap.installations.map((inst) => {
              const configs = snap.instances.filter((i) => i.installation === inst.root);
              const dbs = snap.databases.find((d) => d.installation === inst.root);
              const running = snap.processes.filter((p) => p.installation === inst.root);
              const expanded = open === inst.root;
              return (
                <Fragment key={inst.root}>
                  <tr onClick={() => setOpen(expanded ? null : inst.root)}>
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
                  {expanded && (
                    <tr>
                      <td colSpan={7}>
                        {dbs?.error && <p className="muted">Databases: {dbs.error}</p>}
                        {dbs?.databases.map((d) => (
                          <div key={d.name}>
                            {d.name} <span className="muted">· {mb(d.size)} · filestore {d.filestore ?? "unknown"}{d.filestore_exists === false ? " (missing)" : d.filestore_exists === null ? " (not readable)" : ""}</span>
                          </div>
                        ))}
                        {configs.map((c) => (
                          <div key={c.path} className="row between">
                            <span>{c.name} <span className="muted">· {c.path}{c.problems.length ? ` · ${c.problems.join("; ")}` : ""}</span></span>
                            <span>
                              <button onClick={() => setModulesOf(c.path)}>Modules</button>{" "}
                              <button onClick={() => setEditing({ path: c.path, root: inst.root })}>Edit</button>
                            </span>
                          </div>
                        ))}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      )}
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
