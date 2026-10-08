import { Container as Box, Database, ExternalLink, FolderOpen, Play, Plus, RefreshCw, RotateCw, ScrollText, Square, TerminalSquare, Trash2, Wrench } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { rpc } from "../rpc";
import { Inspector, InspectorSection } from "../shell/Chrome";
import { type DialogSpec, useApp } from "../state/app";
import type { Container, DockerListing, Place } from "../types";
import { ActionMenu, ContextMenu, type MenuEntry } from "../ui/Menu";
import { Badge, Callout, Dot, EmptyState, KV, listKeys, Loading } from "../ui/primitives";
import { View, ViewHead } from "./common";

const where = (p: Place) => p.host ?? (p.volume ? `${p.anonymous ? "anonymous volume" : "volume"} ${p.volume}` : "inside the image");

function menu(c: Container, open: (d: DialogSpec) => void, nav: () => void): MenuEntry[] {
  return [
    c.running ? { label: "Stop…", icon: <Square />, onSelect: () => open({ kind: "docker-action", container: c, action: "stop" }) }
      : { label: "Start…", icon: <Play />, onSelect: () => open({ kind: "docker-action", container: c, action: "start" }) },
    { label: "Restart…", icon: <RotateCw />, disabled: !c.running, onSelect: () => open({ kind: "docker-action", container: c, action: "restart" }) },
    "sep",
    { label: "Logs", icon: <ScrollText />, onSelect: () => open({ kind: "docker-logs", container: c }) },
    { label: "Shell command…", icon: <TerminalSquare />, disabled: !c.running, onSelect: () => open({ kind: "docker-shell", container: c }) },
    { label: "Upgrade modules…", icon: <Wrench />, onSelect: () => open({ kind: "docker-action", container: c, action: "upgrade" }) },
    { label: "New database…", icon: <Plus />, disabled: !c.running || !c.db.container, onSelect: () => open({ kind: "docker-action", container: c, action: "newdb" }) },
    c.db.container ? { label: "Databases", icon: <Database />, onSelect: nav } : null,
    c.app_stack ? "sep" : null,
    c.app_stack ? { label: "Delete stack…", icon: <Trash2 />, danger: true, onSelect: () => open({ kind: "docker-delete", container: c }) } : null,
  ];
}

/** Odoo containers: state, ports, mounts, database, and their operations. */
export function DockerView({ name }: { name?: string }) {
  const app = useApp();
  const [listing, setListing] = useState<DockerListing | null>(null);
  const [loading, setLoading] = useState(false);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      setListing(await rpc.request<DockerListing>("docker.list"));
    } catch (e) {
      app.onError(String((e as Error).message));
    } finally {
      setLoading(false);
    }
  }, [app.onError]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [load, app.versions.docker]);

  const containers = listing?.containers ?? [];
  const selected = containers.find((c) => c.name === name) ?? null;
  const pick = (c: Container) => { app.nav({ view: "docker", name: c.name }); app.inspect(); };
  const notInstalled = listing && !listing.available && listing.error === "docker is not installed";
  const odooPort = (c: Container) => c.ports.find((p) => p.container.startsWith("8069/"))?.host_port;

  return (
    <>
      <View>
        <ViewHead icon={<Box />} title="Docker" subtitle="Odoo in containers: no venv, no system user, nothing installed on this machine."
          actions={<>
            <button className="btn ghost" onClick={load} disabled={loading}><RefreshCw />{loading ? "Reading…" : "Refresh"}</button>
            <button className="btn primary" disabled={!listing?.available} onClick={() => app.setDialog({ kind: "docker-new" })}
              title="Odoo and PostgreSQL in containers, in a new folder under ~/odp-docker"><Plus />New Docker Odoo…</button>
          </>} />
        {!listing ? <Loading>Asking the Docker daemon…</Loading> : notInstalled ? (
          <EmptyState icon={<Box />} title="Docker is not installed">Install Docker to run Odoo in containers. Everything else in Odoo Dev Panel works without it.</EmptyState>
        ) : listing.error ? (
          <Callout tone="bad" title="Docker">{listing.error}</Callout>
        ) : containers.length === 0 ? (
          <EmptyState icon={<Box />} title="No Odoo containers" actions={<button className="btn primary" onClick={() => app.setDialog({ kind: "docker-new" })}><Plus />New Docker Odoo…</button>}>
            Create one: Odoo and its database run in containers, with config and addons folders in {listing.stacks_root}.
          </EmptyState>
        ) : (
          <div className="panel" style={{ overflow: "hidden" }}>
            <div className="list" role="listbox" aria-label="Containers" onKeyDown={listKeys(containers, selected, (a, b) => a.id === b.id, pick)}>
              {containers.map((c) => {
                const items = menu(c, app.setDialog, () => app.nav({ view: "databases", root: `docker:${c.name}` }));
                const port = odooPort(c);
                return (
                  <ContextMenu key={c.id} items={items}>
                    <div className="list-row clickable" role="option" aria-selected={selected?.id === c.id} tabIndex={selected?.id === c.id ? 0 : -1} onClick={() => pick(c)}
                      style={{ minHeight: 52 }}>
                      <Dot tone={c.running ? "ok" : "idle"} live={c.running} />
                      <div className="grow" style={{ display: "grid" }}>
                        <span className="strong truncate">{c.name}{c.app_stack && <span className="dim small"> · stack {c.app_stack}</span>}</span>
                        <span className="meta mono xs truncate">{c.image}{c.compose ? ` · compose ${c.compose.project}/${c.compose.service ?? "?"}` : ""}</span>
                      </div>
                      {c.version && <Badge mono>Odoo {c.version}</Badge>}
                      <span className="meta small nowrap">{c.status}</span>
                      <div className="row-actions">
                        {c.running && port && <button className="btn sm" onClick={(e) => { e.stopPropagation(); app.openPort(port); }}><ExternalLink />:{port}</button>}
                        {c.running ? <button className="btn sm" onClick={(e) => { e.stopPropagation(); app.setDialog({ kind: "docker-action", container: c, action: "stop" }); }}><Square />Stop</button>
                          : <button className="btn sm" onClick={(e) => { e.stopPropagation(); app.setDialog({ kind: "docker-action", container: c, action: "start" }); }}><Play />Start</button>}
                        <ActionMenu items={items} />
                      </div>
                    </div>
                  </ContextMenu>
                );
              })}
            </div>
          </div>
        )}
      </View>
      {selected && (
        <Inspector title={selected.name}>
          <InspectorSection title="State">
            <KV items={[
              ["Status", <span className="row tight"><Dot tone={selected.running ? "ok" : "idle"} />{selected.status}</span>],
              ["Odoo", selected.version],
              ["Image", selected.image, "mono"],
              ["Compose", selected.compose ? `${selected.compose.project}/${selected.compose.service ?? "?"}` : null, "mono"],
            ]} />
          </InspectorSection>
          <InspectorSection title="Ports">
            {selected.ports.length === 0 ? <span className="muted small">none published</span> : selected.ports.map((p) => (
              <div key={`${p.host_port}-${p.container}`} className="row nowrap small">
                {selected.running && p.container.startsWith("8069/") ? <a className="mono" onClick={() => app.openPort(p.host_port)}>localhost:{p.host_port}</a> : <span className="mono">:{p.host_port}</span>}
                <span className="dim mono">→ {p.container}</span>
              </div>
            ))}
          </InspectorSection>
          <InspectorSection title="Mounts">
            <KV items={[
              ...(selected.config ? [["config", <><code>{selected.config.container}</code><br /><span className="dim xs">{where(selected.config)}</span></>] as [React.ReactNode, React.ReactNode]] : []),
              ...selected.addons.map((a) => ["addons", <><code>{a.container}</code><br /><span className="dim xs">{where(a)}</span></>] as [React.ReactNode, React.ReactNode]),
              ...(selected.data ? [["data", <span className="xs">{where(selected.data)}</span>] as [React.ReactNode, React.ReactNode]] : []),
            ]} />
            {selected.compose?.working_dir && <span className="row tight xs dim"><FolderOpen style={{ width: 12, height: 12 }} /><code>{selected.compose.working_dir}</code></span>}
          </InspectorSection>
          <InspectorSection title="Database">
            <KV items={[
              ["Server", selected.db.container ?? selected.db.host, "mono"],
              ["User", selected.db.user, "mono"],
              ["Port", selected.db.port, "mono"],
            ]} />
          </InspectorSection>
          <div className="action-stack">
            {selected.running
              ? <button className="btn" onClick={() => app.setDialog({ kind: "docker-action", container: selected, action: "restart" })}><RotateCw />Restart…</button>
              : <button className="btn primary" onClick={() => app.setDialog({ kind: "docker-action", container: selected, action: "start" })}><Play />Start…</button>}
            <button className="btn" onClick={() => app.setDialog({ kind: "docker-logs", container: selected })}><ScrollText />Logs</button>
            <button className="btn" onClick={() => app.setDialog({ kind: "docker-action", container: selected, action: "upgrade" })}><Wrench />Upgrade modules…</button>
            <button className="btn" disabled={!selected.running || !selected.db.container} onClick={() => app.setDialog({ kind: "docker-action", container: selected, action: "newdb" })}><Plus />New database…</button>
            <button className="btn" disabled={!selected.running} onClick={() => app.setDialog({ kind: "docker-shell", container: selected })}><TerminalSquare />Shell command…</button>
            {selected.db.container && <button className="btn" onClick={() => app.nav({ view: "databases", root: `docker:${selected.name}` })}><Database />Databases</button>}
            {selected.app_stack && <button className="btn danger" onClick={() => app.setDialog({ kind: "docker-delete", container: selected })}><Trash2 />Delete stack…</button>}
          </div>
        </Inspector>
      )}
    </>
  );
}
