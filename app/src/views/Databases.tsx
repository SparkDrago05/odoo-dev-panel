import { Archive, Camera, Copy, Database as DbIcon, Eraser, History, Lock, RefreshCw, RotateCcw, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { rpc } from "../rpc";
import { Inspector, InspectorSection } from "../shell/Chrome";
import { installLabel } from "../shell/Sidebar";
import { useApp } from "../state/app";
import type { Database, DbAction, DbListing, DbSnapshot } from "../types";
import { ActionMenu, ContextMenu, type MenuEntry } from "../ui/Menu";
import { ago, Badge, Callout, CopyButton, Dot, EmptyState, Field, KV, listKeys, Loading, mb } from "../ui/primitives";
import { Panel, View, ViewHead } from "./common";

function fsBadge(d: Database) {
  return d.filestore_exists ? <Badge tone="ok">filestore</Badge> : d.filestore_exists === false ? <Badge tone="warn">no filestore</Badge> : <Badge>filestore unknown</Badge>;
}

export function useDbData(root: string) {
  const { versions, onError } = useApp();
  const [listing, setListing] = useState<DbListing | null>(null);
  const [snaps, setSnaps] = useState<{ snapshots: DbSnapshot[]; error: string | null } | null>(null);
  const [loading, setLoading] = useState(false);
  const load = useCallback(async () => {
    if (!root) return;
    setLoading(true);
    try {
      const l = await rpc.request<DbListing>("db.list", { root });
      setListing(l);
      // Snapshots are read through the run-as user's agent; without it the list stays empty.
      setSnaps(l.agent_running === false ? { snapshots: [], error: "unlock the agent to list snapshots" }
        : await rpc.request<{ snapshots: DbSnapshot[]; error: string | null }>("db.snapshots", { root }));
    } catch (e) {
      onError(String((e as Error).message));
    } finally {
      setLoading(false);
    }
  }, [root, onError]);
  useEffect(() => { setListing(null); setSnaps(null); load(); }, [load, versions.db]);
  return { listing, snaps, loading, load };
}

export function dbMenu(open: (action: DbAction, source?: string, backup?: string) => void, name: string): MenuEntry[] {
  return [
    { label: "Back up…", icon: <Archive />, onSelect: () => open("backup", name) },
    { label: "Snapshot…", icon: <Camera />, onSelect: () => open("snapshot", name), hint: "keeps the newest few" },
    { label: "Clone…", icon: <Copy />, onSelect: () => open("clone", name) },
    "sep",
    { section: "Changes data" },
    { label: "Neutralize…", icon: <Eraser />, onSelect: () => open("neutralize", name), danger: true },
    { label: "Drop…", icon: <Trash2 />, onSelect: () => open("drop", name), danger: true },
  ];
}

/** Databases of one installation (or Docker container) with their snapshots. Every action opens the job dialog
 * with its checks and, for destructive ones, a typed confirmation. */
export function DatabaseBrowser({ root, data, embedded, selected, onSelect }: {
  root: string; data: ReturnType<typeof useDbData>; embedded?: boolean; selected?: string; onSelect?: (db: string) => void;
}) {
  const app = useApp();
  const { listing, snaps, loading, load } = data;
  const owner = app.snap?.installations.find((i) => i.root === root)?.owner;
  const open = (action: DbAction, source?: string, backup?: string) => app.setDialog({ kind: "db", root, action, source, backup });
  const select = (name: string) => (onSelect ? onSelect(name) : app.nav({ view: "databases", root, db: name }));
  const dbs = listing?.databases ?? [];

  return (
    <div className="stack">
      <Panel flush title={<div className="row"><h2>Databases</h2>{listing && listing.agent_running !== false && !listing.error && <span className="count-pill">{dbs.length}</span>}</div>}
        actions={<>
          <button className="btn ghost sm" onClick={load} disabled={loading}><RefreshCw />{loading ? "Loading…" : "Refresh"}</button>
          <button className="btn sm" onClick={() => open("restore")}><Upload />Restore backup…</button>
        </>}>
        {!listing ? <Loading>Listing databases…</Loading> : listing.agent_running === false ? (
          <EmptyState icon={<Lock />} title="Agent locked"
            actions={owner && <button className="btn primary" onClick={() => app.act(`unlocking ${owner}`, async () => { await rpc.request("agent.start", { user: owner }); await load(); })}>Unlock {owner}</button>}>
            Databases of this installation are read through the {owner ?? "run-as"} agent (peer authentication). Unlock it once per boot.
          </EmptyState>
        ) : listing.error ? (
          <div style={{ padding: 14 }}><Callout tone="bad" title="Databases could not be listed">{listing.error}</Callout></div>
        ) : dbs.length === 0 ? (
          <EmptyState icon={<DbIcon />} title="No databases" actions={<button className="btn" onClick={() => open("restore")}><Upload />Restore a backup</button>}>
            No databases are owned by this installation's role.
          </EmptyState>
        ) : (
          <div className="list" role="listbox" aria-label="Databases" onKeyDown={listKeys(dbs.map((d) => d.name), selected, (a, b) => a === b, select)}>
            {dbs.map((d) => {
              const count = snaps?.snapshots.filter((s) => s.database === d.name).length ?? 0;
              return (
                <ContextMenu key={d.name} items={dbMenu(open, d.name)}>
                  <div className="list-row clickable" role="option" tabIndex={selected === d.name || (!selected && d === dbs[0]) ? 0 : -1}
                    aria-selected={!embedded && selected === d.name} onClick={() => select(d.name)}>
                    <DbIcon style={{ width: 15, height: 15, color: "var(--text-3)", flex: "none" }} />
                    <span className="grow mono truncate strong" style={{ fontSize: 12.5 }}>{d.name}</span>
                    {count > 0 && <Badge title="snapshots"><Camera />{count}</Badge>}
                    {fsBadge(d)}
                    <span className="meta mono nowrap" style={{ width: 72, textAlign: "right" }}>{mb(d.size)}</span>
                    <div className="row-actions">
                      <button className="btn sm" onClick={(e) => { e.stopPropagation(); open("backup", d.name); }} title="Back up database and filestore"><Archive />Back up</button>
                      <ActionMenu items={dbMenu(open, d.name)} label={`Actions for ${d.name}`} />
                    </div>
                  </div>
                </ContextMenu>
              );
            })}
          </div>
        )}
      </Panel>

      {snaps && listing?.agent_running !== false && (
        <Panel flush title={<div className="row"><h2>Snapshots</h2><span className="count-pill">{snaps.snapshots.length}</span></div>}>
          {snaps.error && <div style={{ padding: 14 }}><Callout tone="warn">Snapshots could not be listed: {snaps.error}</Callout></div>}
          {!snaps.error && snaps.snapshots.length === 0 && (
            <p className="muted small" style={{ padding: 14 }}>No snapshots. Snapshot a database above{root.startsWith("docker:") ? "" : ', or tick "Snapshot first" on an upgrade run'}.</p>
          )}
          {snaps.snapshots.length > 0 && <SnapshotList snaps={snaps.snapshots} dbs={dbs} open={open} />}
        </Panel>
      )}
      {embedded && <span className="xs dim">Select a database to see its details and every action.</span>}
    </div>
  );
}

/** The database list of one installation, for its Databases tab. */
export function EmbeddedDatabases({ root }: { root: string }) {
  const data = useDbData(root);
  return <DatabaseBrowser root={root} data={data} embedded />;
}

function SnapshotList({ snaps, dbs, open }: { snaps: DbSnapshot[]; dbs: Database[]; open: (a: DbAction, s?: string, b?: string) => void }) {
  return (
    <div className="list">
      {snaps.map((x) => {
        const canRevert = dbs.some((d) => d.name === x.database);
        const items: MenuEntry[] = [
          { label: "Revert database to this…", icon: <RotateCcw />, disabled: !canRevert, onSelect: () => open("revert", x.database ?? undefined, x.path), hint: "keeps the current one" },
          { label: "Restore as new database…", icon: <Upload />, onSelect: () => open("restore", undefined, x.path) },
          "sep",
          { label: "Delete snapshot…", icon: <Trash2 />, danger: true, onSelect: () => open("forget", undefined, x.path) },
        ];
        return (
          <ContextMenu key={x.path} items={items}>
            <div className="list-row">
              <History style={{ width: 15, height: 15, color: "var(--text-3)", flex: "none" }} />
              <div className="grow" style={{ display: "grid" }}>
                <span className="truncate"><span className="mono strong small">{x.database}</span> <span className="dim small">· {x.created_at ? new Date(x.created_at).toLocaleString() : x.name}</span></span>
                <span className="meta mono xs truncate" title={x.path}>{x.path}</span>
              </div>
              {!x.filestore && <Badge tone="warn">no filestore</Badge>}
              <span className="meta mono nowrap">{x.bytes !== null ? mb(x.bytes) : ""}</span>
              <div className="row-actions">
                <button className="btn sm" disabled={!canRevert} title="Replace the database with this snapshot; the current one is kept under a new name"
                  onClick={() => open("revert", x.database ?? undefined, x.path)}><RotateCcw />Revert…</button>
                <ActionMenu items={items} />
              </div>
            </div>
          </ContextMenu>
        );
      })}
    </div>
  );
}

export function DatabasesView({ root: routeRoot, db }: { root?: string; db?: string }) {
  const app = useApp();
  const { snap } = app;
  const [docker, setDocker] = useState<{ root: string; label: string }[]>([]);
  useEffect(() => {
    rpc.request<{ containers: { name: string; db: { container: string | null } }[] }>("docker.list")
      .then((l) => setDocker((l.containers ?? []).filter((c) => c.db.container).map((c) => ({ root: `docker:${c.name}`, label: `Docker · ${c.name}` }))))
      .catch(() => setDocker([]));
  }, [app.versions.docker]);
  const roots = useMemo(() => [
    ...(snap?.installations ?? []).map((i) => ({ root: i.root, label: `Odoo ${i.version ?? "?"} · ${installLabel(i)}` })),
    ...docker,
  ], [snap, docker]);
  const root = routeRoot ?? roots[0]?.root ?? "";
  const data = useDbData(root);
  const detail = { listing: data.listing, snaps: data.snaps?.snapshots ?? [] };

  const d = detail.listing?.databases.find((x) => x.name === db);
  const open = (action: DbAction, source?: string, backup?: string) => app.setDialog({ kind: "db", root, action, source, backup });
  const where = root.startsWith("docker:") ? `container ${root.slice(7)}` : root;
  const own = snap?.installations.find((i) => i.root === root);

  return (
    <>
      <View>
        <ViewHead icon={<DbIcon />} title="Databases" subtitle="Database and filestore are always handled together."
          actions={roots.length > 0 && (
            <Field label="">
              <select value={root} onChange={(e) => app.nav({ view: "databases", root: e.target.value })} aria-label="Installation" style={{ minWidth: 240 }}>
                {roots.map((r) => <option key={r.root} value={r.root}>{r.label}</option>)}
              </select>
            </Field>
          )} />
        {!snap ? <Loading /> : roots.length === 0 ? (
          <EmptyState icon={<DbIcon />} title="Nothing to list">No installation or Docker container with a database was found.</EmptyState>
        ) : (
          <DatabaseBrowser root={root} data={data} selected={db} onSelect={(name) => { app.nav({ view: "databases", root, db: name }); app.inspect(); }} />
        )}
      </View>
      {d && (
        <Inspector title={<span className="mono">{d.name}</span>} actions={<CopyButton text={d.name} label="Copy name" />}>
          <InspectorSection title="Details">
            <KV items={[
              ["Size", mb(d.size), "mono"],
              ["Installation", own ? <a onClick={() => app.nav({ view: "installation", root })}>{installLabel(own)}</a> : where],
              ["Odoo", own?.version ?? null],
              ["Role", own?.pg_role ?? null, "mono"],
              ["Filestore", <span className="row tight"><Dot tone={d.filestore_exists ? "ok" : d.filestore_exists === false ? "warn" : "idle"} />{d.filestore_exists ? "present" : d.filestore_exists === false ? "missing" : "not readable"}</span>],
            ]} />
            {d.filestore && <code className="xs break dim">{d.filestore}</code>}
          </InspectorSection>
          <InspectorSection title="Back up and copy">
            <div className="action-stack">
              <button className="btn primary" onClick={() => open("backup", d.name)}><Archive />Back up…</button>
              <button className="btn" onClick={() => open("snapshot", d.name)}><Camera />Snapshot…</button>
              <button className="btn" onClick={() => open("clone", d.name)}><Copy />Clone…</button>
            </div>
          </InspectorSection>
          <InspectorSection title={`Snapshots (${detail.snaps.filter((s) => s.database === d.name).length})`}>
            {detail.snaps.filter((s) => s.database === d.name).length === 0 ? <span className="muted small">None yet.</span> : (
              <div className="stack tight">
                {detail.snaps.filter((s) => s.database === d.name).map((s) => (
                  <div key={s.path} className="row nowrap">
                    <History style={{ width: 13, height: 13, color: "var(--text-3)" }} />
                    <span className="grow small truncate" title={s.path}>{ago(s.created_at) || s.name}</span>
                    <span className="xs mono dim">{s.bytes !== null ? mb(s.bytes) : ""}</span>
                    <button className="btn ghost sm" onClick={() => open("revert", d.name, s.path)}><RotateCcw />Revert</button>
                  </div>
                ))}
              </div>
            )}
          </InspectorSection>
          <InspectorSection title="Changes data">
            <div className="action-stack">
              <button className="btn danger" onClick={() => open("neutralize", d.name)}><Eraser />Neutralize…</button>
              <button className="btn danger" onClick={() => open("drop", d.name)}><Trash2 />Drop…</button>
            </div>
            <span className="xs dim">Both ask you to type the database name. Drop moves the filestore to a .trash folder.</span>
          </InspectorSection>
        </Inspector>
      )}
    </>
  );
}
