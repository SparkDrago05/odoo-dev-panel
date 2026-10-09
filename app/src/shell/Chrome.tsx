import {
  AlertTriangle, ChevronRight, CircleCheck, Cpu, ExternalLink, Info, Moon, PanelBottom, PanelRight, Search, Square, Sun, TerminalSquare, X, XCircle,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { type ReactNode, useState } from "react";
import { type Route, sessionKey, useApp } from "../state/app";
import { LogConsole } from "../ui/LogConsole";
import { Dot, useNarrow } from "../ui/primitives";
import { Problems } from "../views/Problems";
import { installLabel } from "./Sidebar";

// ---------- Top bar ----------

function useCrumbs(): { label: string; to?: Route }[] {
  const { route, snap, sessions } = useApp();
  const install = (root: string) => snap?.installations.find((i) => i.root === root);
  switch (route.view) {
    case "home": return [{ label: "Overview" }];
    case "installation": {
      const i = install(route.root);
      return [{ label: "Installations", to: { view: "home" } }, { label: i ? installLabel(i) : route.root }];
    }
    case "instance": {
      const inst = snap?.instances.find((i) => i.path === route.path);
      const i = inst?.installation ? install(inst.installation) : undefined;
      return [
        { label: "Installations", to: { view: "home" } },
        ...(i ? [{ label: installLabel(i), to: { view: "installation", root: i.root } as Route }] : []),
        { label: inst?.name ?? route.path },
      ];
    }
    case "databases": return [{ label: "Databases", to: { view: "databases" } }, ...(route.root ? [{ label: route.root.startsWith("docker:") ? route.root.slice(7) : install(route.root) ? installLabel(install(route.root)!) : route.root, to: { view: "databases", root: route.root } as Route }] : []), ...(route.db ? [{ label: route.db }] : [])];
    case "sessions": {
      const s = route.key ? sessions.find((x) => sessionKey(x) === route.key) : undefined;
      return [{ label: "Sessions", to: { view: "sessions" } }, ...(s ? [{ label: s.name }] : [])];
    }
    case "doctor": return [{ label: "Doctor" }];
    case "docker": return [{ label: "Docker", to: { view: "docker" } }, ...(route.name ? [{ label: route.name }] : [])];
    case "services": return [{ label: "Services", to: { view: "services" } }, ...(route.name ? [{ label: route.name }] : [])];
    case "repos": return [{ label: "Repositories", to: { view: "repos" } }, ...(route.path ? [{ label: route.path.split("/").filter(Boolean).pop() ?? route.path }] : [])];
    case "tasks": return [{ label: "Tasks", to: { view: "tasks" } }, ...(route.name ? [{ label: route.name }] : [])];
    case "settings": return [{ label: "Settings" }];
  }
}

export function Topbar({ inspectorAvailable }: { inspectorAvailable: boolean }) {
  const app = useApp();
  const crumbs = useCrumbs();
  const dark = document.documentElement.dataset.theme !== "light";
  const narrow = useNarrow(1120);
  const open = app.prefs.inspector && (!narrow || app.drawer);
  const toggle = () => (narrow ? (open ? app.setDrawer(false) : app.inspect()) : app.setPrefs({ inspector: !app.prefs.inspector }));
  return (
    <header className="topbar">
      <nav className="crumbs" aria-label="Breadcrumb">
        {crumbs.map((c, i) => (
          <span key={i} style={{ display: "contents" }}>
            {i > 0 && <ChevronRight className="sep" aria-hidden />}
            <button type="button" onClick={c.to && i < crumbs.length - 1 ? () => app.nav(c.to!) : undefined}
              aria-current={i === crumbs.length - 1 ? "page" : undefined} title={c.label}>{c.label}</button>
          </span>
        ))}
      </nav>
      <button type="button" className="search-trigger" onClick={() => app.setPalette(true)} aria-label="Open command palette">
        <Search /><span>Search or run a command…</span><kbd>Ctrl K</kbd>
      </button>
      <button type="button" className={`btn ghost icon${open && inspectorAvailable ? " active" : ""}`} disabled={!inspectorAvailable}
        aria-pressed={open} aria-label="Toggle inspector" title="Toggle inspector (Ctrl+I)" onClick={toggle}><PanelRight /></button>
      <button type="button" className="btn ghost icon" aria-label={dark ? "Light theme" : "Dark theme"} title={dark ? "Light theme" : "Dark theme"}
        onClick={() => app.setPrefs({ theme: dark ? "light" : "dark" })}>{dark ? <Sun /> : <Moon />}</button>
    </header>
  );
}

// ---------- Inspector ----------

/** Right-hand detail panel of a view. Only rendered when the view has something selected. */
export function Inspector({ title, actions, children }: { title: ReactNode; actions?: ReactNode; children: ReactNode }) {
  const { prefs, setPrefs, drawer, setDrawer } = useApp();
  const narrow = useNarrow(1120);
  const shown = prefs.inspector && (!narrow || drawer);
  const close = () => (narrow ? setDrawer(false) : setPrefs({ inspector: false }));
  return (
    <AnimatePresence initial={false}>
      {shown && (
        <motion.aside className="inspector" aria-label="Inspector" initial={{ width: 0, opacity: 0 }} animate={{ width: "auto", opacity: 1 }}
          exit={{ width: 0, opacity: 0 }} transition={{ duration: 0.22, ease: [0.2, 0.7, 0.2, 1] }}>
          <div className="inspector-inner">
            <div className="inspector-head">
              <h2>{title}</h2>
              {actions}
              <button className="btn ghost icon sm" aria-label="Close inspector" title="Close inspector (Ctrl+I)" onClick={close}><X /></button>
            </div>
            <div className="inspector-body">{children}</div>
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}

export function InspectorSection({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="inspector-section">
      <div className="row between"><span className="section-title">{title}</span>{actions}</div>
      {children}
    </section>
  );
}

// ---------- Output dock ----------

export function Dock() {
  const app = useApp();
  const s = app.followed;
  const [view, setView] = useState<"output" | "problems">("output");
  const [dragging, setDragging] = useState(false);
  if (!s || !app.prefs.dock || app.route.view === "sessions") return null;
  const height = app.prefs.dockHeight;
  const startDrag = (e: React.PointerEvent) => {
    e.preventDefault();
    setDragging(true);
    const startY = e.clientY;
    const startH = height;
    const move = (ev: PointerEvent) => app.setPrefs({ dockHeight: Math.min(window.innerHeight - 220, Math.max(140, startH - (ev.clientY - startY))) });
    const up = () => { setDragging(false); window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const running = s.state === "running";
  return (
    <motion.section className="dock" aria-label="Output" style={{ height }} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.18 }}>
      <div className={`resize-y${dragging ? " dragging" : ""}`} onPointerDown={startDrag} role="separator" aria-orientation="horizontal" aria-label="Resize output panel" />
      <div className="dock-head">
        <span className="title truncate">
          <TerminalSquare style={{ width: 14, height: 14 }} />
          <Dot tone={running ? "ok" : s.exit_code ? "bad" : "idle"} live={running} />
          <span className="truncate">{s.name}</span>
          <span className="dim mono xs">{s.user}{s.meta?.port ? ` · :${s.meta.port}` : ""}</span>
        </span>
        {!s.pty && (
          <div className="segmented" role="group" aria-label="Output view" style={{ marginLeft: 8 }}>
            <button aria-pressed={view === "output"} onClick={() => setView("output")}>Output</button>
            <button aria-pressed={view === "problems"} onClick={() => setView("problems")}>Problems</button>
          </div>
        )}
        <span className="grow" />
        {running && s.meta?.port && <button className="btn ghost sm" onClick={() => app.openPort(s.meta!.port!)}><ExternalLink />Open</button>}
        {running && <button className="btn ghost sm" onClick={() => app.stopSession(s)}><Square />Stop</button>}
        <button className="btn ghost sm" onClick={() => app.nav({ view: "sessions", key: sessionKey(s) })}>Details</button>
        <button className="btn ghost icon sm" aria-label="Hide output panel" title="Hide (Ctrl+J)" onClick={() => app.setPrefs({ dock: false })}><X /></button>
      </div>
      <div style={{ flex: 1, minHeight: 0 }}>
        {view === "problems" && !s.pty
          ? <div style={{ overflow: "auto", height: "100%" }}><Problems user={s.user} id={s.id} state={s.state} /></div>
          : <LogConsole text={app.log} levels={!s.pty} empty={running ? "Waiting for output…" : "No output."} />}
      </div>
    </motion.section>
  );
}

// ---------- Status bar ----------

export function StatusBar() {
  const app = useApp();
  const agentsUp = app.agents.filter((a) => a.state === "running").length;
  const running = app.sessions.filter((s) => s.state === "running").length;
  return (
    <footer className="statusbar">
      <span className="item"><Cpu />{app.coreDown ? "core stopped" : app.info ? `core v${app.info.version} · ${app.info.user}` : "starting core…"}</span>
      <button className="item" onClick={() => app.nav({ view: "sessions" })} title="Agents and sessions">
        <Dot tone={agentsUp ? "ok" : "idle"} />{agentsUp}/{app.agents.length} agents unlocked
      </button>
      <button className="item" onClick={() => app.nav({ view: "sessions" })}>{running} running</button>
      {app.busy && <span className="item"><span className="spinner" style={{ width: 10, height: 10, borderWidth: 1.5 }} />{app.busy}…</span>}
      <span className="grow" />
      {app.followed && (
        <button className="item" onClick={() => app.setPrefs({ dock: !app.prefs.dock })} title="Toggle output panel (Ctrl+J)">
          <PanelBottom />{app.prefs.dock ? "Hide output" : `Output: ${app.followed.name}`}
        </button>
      )}
      <span className="item">Ctrl K</span>
    </footer>
  );
}

// ---------- Toasts ----------

const TOAST_ICON = { bad: XCircle, ok: CircleCheck, info: Info };
export function Toasts() {
  const { toasts, dismiss } = useApp();
  return (
    <div className="toasts" aria-live="polite">
      <AnimatePresence initial={false}>
        {toasts.map((t) => {
          const Icon = TOAST_ICON[t.tone];
          return (
            <motion.div key={t.id} layout className={`toast ${t.tone}`} role={t.tone === "bad" ? "alert" : "status"}
              initial={{ opacity: 0, y: 10, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, x: 24 }}
              transition={{ duration: 0.18 }}>
              <Icon />
              <span className="msg">{t.message}</span>
              <button className="btn ghost icon sm" aria-label="Dismiss" onClick={() => dismiss(t.id)}><X /></button>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}

// ---------- Banners ----------

export function Banners() {
  const app = useApp();
  const g = app.info?.group;
  return (
    <>
      {app.coreDown && (
        <div className="banner bad" role="alert"><XCircle />The core process exited. Close and reopen the app; running Odoo sessions keep running.</div>
      )}
      {g && !g.active && (
        <div className="banner" role="alert">
          <AlertTriangle />
          {!g.exists ? (
            <span>Group {g.group} is missing: the package is not set up. Reinstall it: <code>sudo apt install --reinstall odoo-dev-panel</code></span>
          ) : !g.member ? (
            <>
              <span className="grow">You are not in the {g.group} group, so the app cannot talk to the agents. One sudo prompt, no logout needed.</span>
              <button className="btn primary sm" onClick={app.joinGroup} title={`Runs: sudo usermod -aG ${g.group} ${app.info?.user}`}>Add me to {g.group}</button>
            </>
          ) : (
            <span>You are in {g.group}, but this app session cannot use it yet. Close and reopen the app, or log out and back in.</span>
          )}
        </div>
      )}
    </>
  );
}
