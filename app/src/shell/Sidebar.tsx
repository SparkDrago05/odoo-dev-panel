import {
  Activity, Boxes, ChevronRight, Container, Copy, Database, FileCog, FolderTree, GitCompare, HeartPulse, Home, PanelLeftClose,
  FolderGit2, Gauge, ListChecks, PanelLeftOpen, Pencil, Play, Plus, RefreshCw, Server, Settings, Square, Stethoscope,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { type ReactNode, useMemo, useRef, useState } from "react";
import { type Route, useApp } from "../state/app";
import type { Installation, Instance } from "../types";
import { ContextMenu, type MenuEntry } from "../ui/Menu";
import { Dot, useNarrow } from "../ui/primitives";

export const NAV: { view: Route["view"]; label: string; icon: ReactNode; key: string }[] = [
  { view: "home", label: "Overview", icon: <Home />, key: "1" },
  { view: "sessions", label: "Sessions", icon: <Activity />, key: "2" },
  { view: "databases", label: "Databases", icon: <Database />, key: "3" },
  { view: "doctor", label: "Doctor", icon: <Stethoscope />, key: "4" },
  { view: "docker", label: "Docker", icon: <Container />, key: "5" },
  { view: "services", label: "Services", icon: <Server />, key: "6" },
  { view: "repos", label: "Repositories", icon: <FolderGit2 />, key: "7" },
  { view: "tasks", label: "Tasks", icon: <ListChecks />, key: "8" },
  { view: "perf", label: "Performance", icon: <Gauge />, key: "9" },
];

export const installLabel = (i: Installation) => (i.adopted ? i.name : i.root.split("/").filter(Boolean).pop() ?? i.root);
export const shortVersion = (v: string | null) => (v ? v.replace(/\.0$/, "") : "?");

export function installTone(i: Installation): "ok" | "bad" | "idle" {
  return i.venv_ok === false ? "bad" : i.venv_ok === null ? "idle" : "ok";
}

export function Sidebar() {
  const app = useApp();
  const { prefs, setPrefs, route, nav, snap, sessions, doctor } = app;
  const narrow = useNarrow();
  // Below 900 px the sidebar folds to icons by itself; the saved preference is kept for wider windows.
  const collapsed = prefs.sidebarCollapsed || narrow;
  const [dragging, setDragging] = useState(false);
  const treeRef = useRef<HTMLDivElement>(null);

  const running = sessions.filter((s) => s.state === "running").length;
  const doctorErrors = doctor.report?.counts.error ?? 0;
  const expanded = new Set(prefs.expanded);
  const toggle = (root: string, open?: boolean) => {
    const next = new Set(expanded);
    if (open ?? !next.has(root)) next.add(root); else next.delete(root);
    setPrefs({ expanded: [...next] });
  };

  const byInstall = useMemo(() => {
    const m = new Map<string, Instance[]>();
    for (const i of snap?.instances ?? []) if (i.installation) m.set(i.installation, [...(m.get(i.installation) ?? []), i]);
    return m;
  }, [snap]);
  const orphans = snap?.instances.filter((i) => !i.installation) ?? [];

  const startDrag = (e: React.PointerEvent) => {
    e.preventDefault();
    setDragging(true);
    const startX = e.clientX;
    const startW = prefs.sidebarWidth;
    const move = (ev: PointerEvent) => setPrefs({ sidebarWidth: Math.min(420, Math.max(200, startW + ev.clientX - startX)) });
    const up = () => { setDragging(false); window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  // Up/Down move through visible tree items, Right opens, Left closes or goes to the parent.
  const treeKeys = (e: React.KeyboardEvent) => {
    const items = [...(treeRef.current?.querySelectorAll<HTMLElement>('[role="treeitem"]') ?? [])];
    const i = items.indexOf(document.activeElement as HTMLElement);
    if (i < 0) return;
    const el = items[i];
    if (e.key === "ArrowDown") { e.preventDefault(); items[Math.min(items.length - 1, i + 1)]?.focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); items[Math.max(0, i - 1)]?.focus(); }
    else if (e.key === "ArrowRight" && el.dataset.root && el.getAttribute("aria-expanded") === "false") { e.preventDefault(); toggle(el.dataset.root, true); }
    else if (e.key === "ArrowLeft") {
      e.preventDefault();
      if (el.dataset.root && el.getAttribute("aria-expanded") === "true") toggle(el.dataset.root, false);
      else if (el.dataset.parent) items.find((x) => x.dataset.root === el.dataset.parent)?.focus();
    } else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); el.click(); }
  };

  const width = collapsed ? 56 : prefs.sidebarWidth;
  return (
    <motion.nav
      className={`sidebar${collapsed ? " collapsed" : ""}`}
      aria-label="Main"
      animate={{ width }}
      initial={false}
      transition={dragging ? { duration: 0 } : { duration: 0.22, ease: [0.2, 0.7, 0.2, 1] }}
    >
      <div className="sidebar-head">
        <span className="logo" aria-hidden><Boxes /></span>
        <span className="brand grow">Odoo Dev Panel</span>
        {!collapsed && (
          <button className="btn ghost icon sm" aria-label="Collapse sidebar" title="Collapse sidebar (Ctrl+B)" onClick={() => setPrefs({ sidebarCollapsed: true })}>
            <PanelLeftClose />
          </button>
        )}
      </div>
      <div className="sidebar-scroll">
        {collapsed && !narrow && (
          <button className="nav-item" aria-label="Expand sidebar" title="Expand sidebar (Ctrl+B)" onClick={() => setPrefs({ sidebarCollapsed: false })}>
            <PanelLeftOpen />
          </button>
        )}
        {NAV.map((n) => (
          <button key={n.view} className="nav-item" aria-current={route.view === n.view ? "page" : undefined}
            title={collapsed ? `${n.label} (Ctrl+${n.key})` : undefined} onClick={() => nav({ view: n.view } as Route)}>
            {n.icon}<span className="label">{n.label}</span>
            {n.view === "sessions" && running > 0 && <span className="count-pill ok">{running}</span>}
            {n.view === "doctor" && doctorErrors > 0 && <span className="count-pill bad">{doctorErrors}</span>}
          </button>
        ))}

        <div className="nav-section">
          <span className="grow">Installations</span>
          <button className="btn ghost icon" aria-label="Rescan" title="Scan again" onClick={() => app.scan()} disabled={app.scanning}>
            <RefreshCw className={app.scanning ? "spin" : undefined} />
          </button>
          <button className="btn ghost icon" aria-label="New installation" title="New installation…" onClick={() => app.setDialog({ kind: "provision" })}>
            <Plus />
          </button>
        </div>
        {collapsed ? (
          <button className="nav-item" title="Installations" aria-current={route.view === "installation" || route.view === "instance" ? "page" : undefined}
            onClick={() => (narrow ? nav({ view: "home" }) : setPrefs({ sidebarCollapsed: false }))}><FolderTree /></button>
        ) : (
          <div className="tree" role="tree" aria-label="Installations" ref={treeRef} onKeyDown={treeKeys}>
            {!snap && <div className="tree-item dim"><span className="spinner" /> Scanning…</div>}
            {snap?.installations.length === 0 && <div className="tree-item dim">None found</div>}
            {snap?.installations.map((inst) => (
              <InstallNode key={inst.root} inst={inst} instances={byInstall.get(inst.root) ?? []} open={expanded.has(inst.root)} toggle={() => toggle(inst.root)} />
            ))}
            {orphans.length > 0 && (
              <>
                <div className="nav-section" style={{ paddingTop: 10 }}>Orphan configs</div>
                {orphans.map((o) => (
                  <div key={o.path} role="treeitem" tabIndex={-1} className="tree-item" title={o.path}
                    aria-selected={route.view === "instance" && route.path === o.path}
                    onClick={() => app.setDialog({ kind: "config", path: o.path, root: null })}>
                    <FileCog style={{ width: 14, height: 14, flex: "none" }} />
                    <span className="name">{o.name}</span>
                    {o.version_hint && <span className="tag">{o.version_hint}</span>}
                  </div>
                ))}
              </>
            )}
          </div>
        )}
      </div>
      <div className="sidebar-foot">
        <button className="nav-item" aria-current={route.view === "settings" ? "page" : undefined} onClick={() => nav({ view: "settings" })} title={collapsed ? "Settings" : undefined}>
          <Settings /><span className="label">Settings</span>
        </button>
      </div>
      {!collapsed && <div className={`resize-x${dragging ? " dragging" : ""}`} onPointerDown={startDrag} role="separator" aria-orientation="vertical" aria-label="Resize sidebar" />}
    </motion.nav>
  );
}

function InstallNode({ inst, instances, open, toggle }: { inst: Installation; instances: Instance[]; open: boolean; toggle: () => void }) {
  const app = useApp();
  const { route, nav } = app;
  const selected = route.view === "installation" && route.root === inst.root;
  const anyRunning = instances.some((i) => app.instanceState(i.path).running);
  const menu: MenuEntry[] = [
    { label: "Open", icon: <FolderTree />, onSelect: () => nav({ view: "installation", root: inst.root }) },
    { label: "Databases", icon: <Database />, onSelect: () => nav({ view: "databases", root: inst.root }) },
    { label: "Diagnostics", icon: <HeartPulse />, onSelect: () => nav({ view: "installation", root: inst.root, tab: "doctor" }) },
    { label: "Compare with…", icon: <GitCompare />, onSelect: () => app.setDialog({ kind: "compare", mode: "installations", path: instances[0]?.path }) },
    "sep",
    inst.venv_ok === false && { label: "Repair venv…", icon: <Stethoscope />, onSelect: () => app.setDialog({ kind: "repair-venv", root: inst.root }) },
    { label: "Copy path", icon: <Copy />, onSelect: () => navigator.clipboard.writeText(inst.root) },
  ];
  return (
    <>
      <ContextMenu items={menu}>
        <div role="treeitem" tabIndex={selected ? 0 : -1} aria-expanded={open} aria-selected={selected} data-root={inst.root}
          className="tree-item" title={`${inst.root}${inst.venv_ok === false ? " · venv broken" : ""}`}
          onClick={() => { nav({ view: "installation", root: inst.root }); if (!open) toggle(); }}>
          <ChevronRight className={`chev${open ? " open" : ""}`} onClick={(e) => { e.stopPropagation(); toggle(); }} aria-hidden />
          <span className="ver">{shortVersion(inst.version)}</span>
          <span className="name">{installLabel(inst)}</span>
          {inst.venv_ok === false ? <Dot tone="bad" title="venv broken" /> : anyRunning ? <Dot tone="ok" live title="running" /> : null}
        </div>
      </ContextMenu>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div className="tree-children" role="group" initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.18, ease: [0.2, 0.7, 0.2, 1] }}>
            {instances.length === 0 && <div className="tree-item child dim">No configs</div>}
            {instances.map((i) => <InstanceNode key={i.path} inst={i} parent={inst.root} />)}
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}

function InstanceNode({ inst, parent }: { inst: Instance; parent: string }) {
  const app = useApp();
  const state = app.instanceState(inst.path);
  const selected = app.route.view === "instance" && app.route.path === inst.path;
  const menu: MenuEntry[] = [
    state.running && state.session
      ? { label: "Stop", icon: <Square />, onSelect: () => app.stopSession(state.session!) }
      : { label: "Start", icon: <Play />, onSelect: () => app.quickStart(inst.path), disabled: state.running },
    { label: "Run with options…", icon: <Play />, onSelect: () => app.setDialog({ kind: "run", instance: inst.path }) },
    "sep",
    { label: "Edit config", icon: <Pencil />, onSelect: () => app.setDialog({ kind: "config", path: inst.path, root: inst.installation }) },
    { label: "Modules", icon: <Boxes />, onSelect: () => app.setDialog({ kind: "modules", path: inst.path }) },
    { label: "Compare…", icon: <GitCompare />, onSelect: () => app.setDialog({ kind: "compare", path: inst.path }) },
    "sep",
    { label: "Copy config path", icon: <Copy />, onSelect: () => navigator.clipboard.writeText(inst.path) },
  ];
  return (
    <ContextMenu items={menu}>
      <div role="treeitem" tabIndex={-1} aria-selected={selected} data-parent={parent} className="tree-item child" title={inst.path}
        onClick={() => app.nav({ view: "instance", path: inst.path })}>
        {state.running ? <Dot tone="ok" live={!state.external} /> : inst.problems.length ? <Dot tone="warn" title={inst.problems.join("; ")} /> : <Dot />}
        <span className="name">{inst.name}</span>
        {state.running && state.port && <span className="tag">:{state.port}</span>}
      </div>
    </ContextMenu>
  );
}
