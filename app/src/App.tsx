import { motion, MotionConfig } from "motion/react";
import { Component, type ReactNode, useEffect } from "react";
import { Banners, Dock, StatusBar, Toasts, Topbar } from "./shell/Chrome";
import { CommandPalette } from "./shell/CommandPalette";
import { DialogHost } from "./shell/DialogHost";
import { NAV, Sidebar } from "./shell/Sidebar";
import { AppProvider, type Route, useApp } from "./state/app";
import { DatabasesView } from "./views/Databases";
import { DockerView } from "./views/Docker";
import { DoctorView } from "./views/Doctor";
import { HomeView } from "./views/Home";
import { InstallationView, InstanceView } from "./views/Installation";
import { RepositoriesView } from "./views/Repositories";
import { ServicesView } from "./views/Services";
import { SessionsView } from "./views/Sessions";
import { SettingsView } from "./views/Settings";

function CurrentView({ route }: { route: Route }) {
  switch (route.view) {
    case "home": return <HomeView />;
    case "installation": return <InstallationView root={route.root} tab={route.tab} repo={route.repo} module={route.module} />;
    case "instance": return <InstanceView path={route.path} />;
    case "databases": return <DatabasesView root={route.root} db={route.db} />;
    case "sessions": return <SessionsView selectedKey={route.key} />;
    case "doctor": return <DoctorView finding={route.finding} />;
    case "docker": return <DockerView name={route.name} />;
    case "services": return <ServicesView name={route.name} />;
    case "repos": return <RepositoriesView path={route.path} />;
    case "settings": return <SettingsView />;
  }
}

/** Views that show an inspector when something is selected. */
function hasInspector(r: Route) {
  return (r.view === "instance") || (r.view === "databases" && !!r.db) || (r.view === "doctor" && !!r.finding)
    || (r.view === "docker" && !!r.name) || (r.view === "services" && !!r.name)
    || (r.view === "repos" && !!r.path) || (r.view === "installation" && r.tab === "repos" && !!r.repo) || (r.view === "installation" && r.tab === "modules" && !!r.module);
}

function useShortcuts() {
  const app = useApp();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      const typing = (e.target as HTMLElement)?.closest?.("input, textarea, select, [contenteditable]");
      if (mod && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "k") { e.preventDefault(); app.setPalette(!app.palette); return; }
      // Everything else only when no dialog is open, so shortcuts never act behind a modal.
      if (app.dialog || app.password || app.confirmReq || app.palette) return;
      if (mod && !e.shiftKey && e.key.toLowerCase() === "b") { e.preventDefault(); app.setPrefs({ sidebarCollapsed: !app.prefs.sidebarCollapsed }); }
      else if (mod && !e.shiftKey && e.key.toLowerCase() === "i" && !typing) {
        e.preventDefault();
        if (window.innerWidth < 1120) { if (app.drawer) app.setDrawer(false); else app.inspect(); } else app.setPrefs({ inspector: !app.prefs.inspector });
      }
      else if (mod && !e.shiftKey && e.key.toLowerCase() === "j") { e.preventDefault(); app.setPrefs({ dock: !app.prefs.dock }); }
      else if (mod && !e.shiftKey && /^[1-7]$/.test(e.key)) { e.preventDefault(); app.nav({ view: NAV[Number(e.key) - 1].view } as Route); }
      else if (e.altKey && e.key === "ArrowLeft" && !typing) { e.preventDefault(); app.back(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [app]);
}

function Shell() {
  const app = useApp();
  useShortcuts();
  const r = app.route;
  // One key per page (not per selection), so selecting a row does not replay the page transition.
  const pageKey = r.view === "installation" ? `i:${r.root}` : r.view === "instance" ? `c:${r.path}` : r.view;
  return (
    <div className="app">
      <div className="app-body">
        <Sidebar />
        <main className="main">
          <Topbar inspectorAvailable={hasInspector(r)} />
          <Banners />
          {/* Enter-only transition: the new page is there at once, nothing waits for the old one to fade out. */}
          <motion.div key={pageKey} className="workspace" initial={{ opacity: 0.4, y: 3 }} animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.16, ease: [0.2, 0.7, 0.2, 1] }}>
            <CurrentView route={r} />
          </motion.div>
          <Dock />
        </main>
      </div>
      <StatusBar />
      <Toasts />
      <DialogHost />
      {app.palette && <CommandPalette />}
    </div>
  );
}

/** A render error shows what failed and a way back, instead of an empty window. Odoo sessions are not affected. */
class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="empty" style={{ height: "100vh", alignContent: "center" }}>
        <h3>The window hit an error</h3>
        <p>Running Odoo sessions and agents are not affected.</p>
        <pre className="block" style={{ maxWidth: 720, textAlign: "left" }}>{String(this.state.error.stack ?? this.state.error)}</pre>
        <div className="row"><button className="btn primary" onClick={() => location.reload()}>Reload the window</button></div>
      </div>
    );
  }
}

export default function App() {
  return (
    <ErrorBoundary>
      <MotionConfig reducedMotion="user">
        <AppProvider>
          <Shell />
        </AppProvider>
      </MotionConfig>
    </ErrorBoundary>
  );
}
