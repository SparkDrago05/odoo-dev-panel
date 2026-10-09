import * as RD from "@radix-ui/react-dialog";
import {
  Activity, Boxes, Container, Copy, CornerDownLeft, Database, FileCog, FolderGit2, FolderTree, GitCompare, Moon, PanelBottom, PanelLeft, PanelRight, Pencil,
  Play, Plus, RefreshCw, Search, Server, Settings, Square, Stethoscope, Sun, TerminalSquare,
} from "lucide-react";
import { motion } from "motion/react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { type Route, sessionKey, useApp } from "../state/app";
import { installLabel, NAV } from "./Sidebar";

export type Command = { id: string; group: string; label: string; detail?: string; icon: ReactNode; danger?: boolean; keywords?: string; run: () => void };

/** Rank: every word must match; earlier and word-start matches rank higher. */
export function rank(commands: Command[], query: string): Command[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return commands;
  const scored: [number, Command][] = [];
  for (const c of commands) {
    const hay = `${c.label} ${c.detail ?? ""} ${c.keywords ?? ""}`.toLowerCase();
    let score = 0;
    let ok = true;
    for (const w of words) {
      const i = hay.indexOf(w);
      if (i < 0) { ok = false; break; }
      score += i === 0 ? 0 : /\s|\/|-|_|\./.test(hay[i - 1]) ? 1 : 3 + i / 100;
    }
    if (ok) scored.push([score + (c.label.toLowerCase().includes(words.join(" ")) ? -1 : 0), c]);
  }
  return scored.sort((a, b) => a[0] - b[0]).map(([, c]) => c);
}

function useCommands(): Command[] {
  const app = useApp();
  const { snap, sessions, nav } = app;
  return useMemo(() => {
    const c: Command[] = [];
    for (const n of NAV) c.push({ id: `go-${n.view}`, group: "Go to", label: n.label, detail: `Ctrl+${n.key}`, icon: n.icon, run: () => nav({ view: n.view } as Route) });
    c.push({ id: "go-settings", group: "Go to", label: "Settings", icon: <Settings />, run: () => nav({ view: "settings" }) });

    for (const s of sessions.filter((x) => x.state === "running")) {
      c.push({ id: `out-${sessionKey(s)}`, group: "Running", label: `Show output: ${s.name}`, detail: `${s.user}${s.meta?.port ? ` · :${s.meta.port}` : ""}`, icon: <TerminalSquare />, run: () => { app.follow(s); nav({ view: "sessions", key: sessionKey(s) }); } });
      c.push({ id: `stop-${sessionKey(s)}`, group: "Running", label: `Stop: ${s.name}`, detail: s.user, icon: <Square />, run: () => app.stopSession(s) });
      if (s.meta?.port) c.push({ id: `open-${sessionKey(s)}`, group: "Running", label: `Open in browser: ${s.name}`, detail: `http://localhost:${s.meta.port}`, icon: <Play />, run: () => app.openPort(s.meta!.port!) });
    }

    for (const i of snap?.installations ?? []) {
      const label = installLabel(i);
      c.push({ id: `inst-${i.root}`, group: "Installations", label: `Odoo ${i.version ?? "?"} · ${label}`, detail: i.root, icon: <FolderTree />, keywords: "installation open", run: () => nav({ view: "installation", root: i.root }) });
      c.push({ id: `repos-${i.root}`, group: "Repositories", label: `Repositories of ${label}`, detail: i.root, icon: <FolderGit2 />, keywords: "git branch", run: () => nav({ view: "installation", root: i.root, tab: "repos" }) });
      c.push({ id: `export-${i.root}`, group: "Repositories", label: `Export ${label} as a profile…`, detail: i.root, icon: <FolderGit2 />, keywords: "profile bundle setup", run: () => app.setDialog({ kind: "profile-export", root: i.root }) });
      c.push({ id: `apply-${i.root}`, group: "Repositories", label: `Apply a profile to ${label}…`, detail: "clone its repositories", icon: <FolderGit2 />, keywords: "profile bundle", run: () => app.setDialog({ kind: "profile-apply", installation: i.root }) });
      c.push({ id: `repo-add-${i.root}`, group: "Repositories", label: `Add repository to ${label}…`, detail: "clone or register", icon: <FolderGit2 />, keywords: "git clone", run: () => app.setDialog({ kind: "repo-add", installation: i.root }) });
      c.push({ id: `dbs-${i.root}`, group: "Databases", label: `Databases of ${label}`, detail: i.root, icon: <Database />, run: () => nav({ view: "databases", root: i.root }) });
      if (i.venv_ok === false) c.push({ id: `repair-${i.root}`, group: "Fix", label: `Repair venv of ${label}`, detail: i.root, icon: <Stethoscope />, run: () => app.setDialog({ kind: "repair-venv", root: i.root }) });
    }
    for (const e of snap?.databases ?? []) {
      for (const d of e.databases) c.push({ id: `db-${e.installation}-${d.name}`, group: "Databases", label: d.name, detail: e.installation, icon: <Database />, keywords: "database open", run: () => nav({ view: "databases", root: e.installation, db: d.name }) });
    }
    for (const inst of snap?.instances ?? []) {
      if (!inst.installation) {
        c.push({ id: `cfg-${inst.path}`, group: "Configs", label: `Edit config ${inst.name}`, detail: inst.path, icon: <FileCog />, run: () => app.setDialog({ kind: "config", path: inst.path, root: null }) });
        continue;
      }
      const st = app.instanceState(inst.path);
      c.push({ id: `i-${inst.path}`, group: "Instances", label: inst.name, detail: inst.path, icon: <Boxes />, keywords: "instance open", run: () => nav({ view: "instance", path: inst.path }) });
      if (!st.running) c.push({ id: `start-${inst.path}`, group: "Instances", label: `Start ${inst.name}`, detail: inst.path, icon: <Play />, run: () => app.quickStart(inst.path) });
      c.push({ id: `run-${inst.path}`, group: "Instances", label: `Run ${inst.name} with options…`, detail: "-u, -i, --dev, database, port", icon: <Play />, keywords: "upgrade module install", run: () => app.setDialog({ kind: "run", instance: inst.path }) });
      c.push({ id: `edit-${inst.path}`, group: "Configs", label: `Edit config ${inst.name}`, detail: inst.path, icon: <Pencil />, run: () => app.setDialog({ kind: "config", path: inst.path, root: inst.installation }) });
      c.push({ id: `mod-${inst.path}`, group: "Modules", label: `Modules of ${inst.name}`, detail: inst.path, icon: <Boxes />, keywords: "dependencies", run: () => app.setDialog({ kind: "modules", path: inst.path }) });
    }

    c.push(
      { id: "profiles", group: "Actions", label: "Profiles and bundles", icon: <Settings />, keywords: "profile bundle org overlay", run: () => nav({ view: "settings" }) },
      { id: "new-install", group: "Actions", label: "New Odoo installation…", icon: <Plus />, keywords: "provision create", run: () => app.setDialog({ kind: "provision" }) },
      { id: "new-docker", group: "Actions", label: "New Docker Odoo…", icon: <Container />, keywords: "compose stack", run: () => app.setDialog({ kind: "docker-new" }) },
      { id: "run", group: "Actions", label: "Run Odoo…", icon: <Play />, keywords: "start upgrade install module", run: () => app.setDialog({ kind: "run" }) },
      { id: "doctor", group: "Actions", label: "Run Doctor checks", icon: <Stethoscope />, keywords: "diagnostics health", run: () => { nav({ view: "doctor" }); app.runDoctor(); } },
      { id: "compare", group: "Actions", label: "Compare environments…", icon: <GitCompare />, keywords: "diff", run: () => app.setDialog({ kind: "compare" }) },
      { id: "rescan", group: "Actions", label: "Scan for installations again", icon: <RefreshCw />, keywords: "discover refresh", run: () => app.scan() },
      { id: "repo-fetch-all", group: "Actions", label: "Fetch all repositories…", icon: <FolderGit2 />, keywords: "git fetch bulk", run: () => app.setDialog({ kind: "repo-op", op: "fetch", bulk: true }) },
      { id: "repo-pull-all", group: "Actions", label: "Pull all repositories (fast-forward)…", icon: <FolderGit2 />, keywords: "git pull bulk update", run: () => app.setDialog({ kind: "repo-op", op: "pull", bulk: true }) },
      { id: "services", group: "Actions", label: "systemd services", icon: <Server />, run: () => nav({ view: "services" }) },
      { id: "theme", group: "View", label: document.documentElement.dataset.theme === "light" ? "Dark theme" : "Light theme", icon: document.documentElement.dataset.theme === "light" ? <Moon /> : <Sun />, keywords: "appearance", run: () => app.setPrefs({ theme: document.documentElement.dataset.theme === "light" ? "dark" : "light" }) },
      { id: "sidebar", group: "View", label: "Toggle sidebar", detail: "Ctrl+B", icon: <PanelLeft />, run: () => app.setPrefs({ sidebarCollapsed: !app.prefs.sidebarCollapsed }) },
      { id: "inspector", group: "View", label: "Toggle inspector", detail: "Ctrl+I", icon: <PanelRight />, run: () => app.setPrefs({ inspector: !app.prefs.inspector }) },
      { id: "dock", group: "View", label: "Toggle output panel", detail: "Ctrl+J", icon: <PanelBottom />, run: () => app.setPrefs({ dock: !app.prefs.dock }) },
      { id: "sessions", group: "View", label: "Agents and sessions", icon: <Activity />, run: () => nav({ view: "sessions" }) },
    );
    if (app.followed) c.push({ id: "copy-cmd", group: "Running", label: `Copy command of ${app.followed.name}`, icon: <Copy />, run: () => navigator.clipboard.writeText(app.followed!.argv.join(" ")) });
    return c;
  }, [app, snap, sessions, nav]);
}

/** Ctrl+K: every navigation target and action. Commands run the same code as the buttons, so privileged and destructive
 * actions still go through their dialogs, checks and typed confirmations. Typed text is only ever a filter. */
export function CommandPalette() {
  const app = useApp();
  const commands = useCommands();
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const shown = useMemo(() => rank(commands, query).slice(0, 80), [commands, query]);
  useEffect(() => setIndex(0), [query]);
  useEffect(() => {
    listRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }, [index]);

  const close = () => app.setPalette(false);
  const choose = (c: Command | undefined) => {
    if (!c) return;
    close();
    // Let the palette unmount (and return focus) before a dialog opens.
    setTimeout(() => c.run(), 0);
  };

  let lastGroup = "";
  return (
    <RD.Root open onOpenChange={(o) => { if (!o) close(); }}>
      <RD.Portal>
        <RD.Overlay asChild><motion.div className="overlay" style={{ background: "rgb(0 0 0 / 0.25)" }} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.12 }} /></RD.Overlay>
        <RD.Content asChild aria-describedby={undefined}>
          <motion.div className="palette" initial={{ opacity: 0, x: "-50%", y: -6, scale: 0.985 }} animate={{ opacity: 1, x: "-50%", y: 0, scale: 1 }} transition={{ duration: 0.16, ease: [0.2, 0.7, 0.2, 1] }}>
            <RD.Title className="sr-only" style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" }}>Command palette</RD.Title>
            <div className="palette-input">
              <Search />
              <input autoFocus value={query} placeholder="Search installations, databases, sessions and actions…" aria-label="Command"
                role="combobox" aria-expanded aria-controls="palette-list" aria-activedescendant={shown[index] ? `cmd-${index}` : undefined}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "ArrowDown") { e.preventDefault(); setIndex((i) => Math.min(i + 1, shown.length - 1)); }
                  else if (e.key === "ArrowUp") { e.preventDefault(); setIndex((i) => Math.max(i - 1, 0)); }
                  else if (e.key === "Enter") { e.preventDefault(); choose(shown[index]); }
                }} />
            </div>
            <div className="palette-list" id="palette-list" role="listbox" ref={listRef}>
              {shown.length === 0 && <div className="empty" style={{ padding: 24 }}>No match for “{query}”.</div>}
              {shown.map((c, i) => {
                const head = !query && c.group !== lastGroup ? c.group : null;
                lastGroup = c.group;
                return (
                  <div key={c.id}>
                    {head && <div className="palette-group">{head}</div>}
                    <div id={`cmd-${i}`} role="option" aria-selected={i === index} className="palette-item" onMouseMove={() => setIndex(i)} onClick={() => choose(c)}>
                      <span className={`ico${c.danger ? " danger" : ""}`}>{c.icon}</span>
                      <span className="text"><span className="t">{c.label}</span>{c.detail && <span className="d">{c.detail}</span>}</span>
                      {query && <span className="badge">{c.group}</span>}
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="palette-foot">
              <span><kbd>↑</kbd><kbd>↓</kbd> move</span>
              <span><kbd><CornerDownLeft style={{ width: 10, height: 10 }} /></kbd> run</span>
              <span><kbd>Esc</kbd> close</span>
            </div>
          </motion.div>
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}
