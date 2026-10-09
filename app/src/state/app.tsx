import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { rpc } from "../rpc";
import type { Agent, AppInfo, Container, DbAction, DoctorReport, Session, Snapshot } from "../types";

// ---------- Navigation ----------

export type InstallTab = "overview" | "instances" | "repos" | "databases" | "modules" | "doctor";
export type Route =
  | { view: "home" }
  | { view: "installation"; root: string; tab?: InstallTab; repo?: string }
  | { view: "instance"; path: string }
  | { view: "databases"; root?: string; db?: string }
  | { view: "sessions"; key?: string }
  | { view: "doctor"; finding?: string }
  | { view: "docker"; name?: string }
  | { view: "services"; name?: string }
  | { view: "repos"; path?: string }
  | { view: "settings" };

export const sessionKey = (s: { user: string; id: string }) => `${s.user}/${s.id}`;

// ---------- Dialogs opened from anywhere (views, explorer, command palette) ----------

export type DockerAction = "start" | "stop" | "restart" | "upgrade" | "newdb";
export type DialogSpec =
  | { kind: "provision" }
  | { kind: "config"; path: string; root: string | null }
  | { kind: "modules"; path: string }
  | { kind: "compare"; path?: string; mode?: "configs" | "installations" | "databases" }
  | { kind: "db"; root: string; action: DbAction; source?: string; backup?: string }
  | { kind: "run"; instance?: string }
  | { kind: "repair-venv"; root: string }
  | { kind: "fix-perms"; root: string }
  | { kind: "docker-new" }
  | { kind: "docker-action"; container: Container; action: DockerAction }
  | { kind: "docker-logs"; container: Container }
  | { kind: "docker-shell"; container: Container }
  | { kind: "docker-delete"; container: Container }
  | { kind: "repo-op"; op: "fetch" | "pull" | "switch" | "checkout"; repos?: string[]; bulk?: boolean; installation?: string }
  | { kind: "repo-add"; installation: string }
  | { kind: "repo-diff"; path: string };

// ---------- Preferences ----------

export type Prefs = {
  theme: "dark" | "light" | "system";
  density: "comfortable" | "compact";
  sidebarWidth: number;
  sidebarCollapsed: boolean;
  inspector: boolean;
  dock: boolean;
  dockHeight: number;
  expanded: string[];
};
const DEFAULT_PREFS: Prefs = {
  theme: "dark", density: "comfortable", sidebarWidth: 264, sidebarCollapsed: false,
  inspector: true, dock: true, dockHeight: 230, expanded: [],
};

function readStore<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? { ...fallback, ...JSON.parse(raw) } : fallback;
  } catch {
    return fallback;
  }
}
function writeStore(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage can be blocked */ }
}

export function resolvedTheme(theme: Prefs["theme"]): "dark" | "light" {
  if (theme !== "system") return theme;
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

// ---------- Toasts ----------

export type Toast = { id: number; tone: "bad" | "ok" | "info"; message: string };
export type PasswordRequest = { prompt: string; user: string | null; purpose?: string; resolve: (password: string | null) => void };
export type ConfirmRequest = { title: string; body: ReactNode; confirm: string; danger?: boolean; resolve: (ok: boolean) => void };

const LOG_LIMIT = 400_000;
// Terminal control sequences and carriage returns from PTY sessions; the log pane shows plain text.
const ANSI = /\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r/g;

type Ctx = ReturnType<typeof useAppState>;
const AppContext = createContext<Ctx | null>(null);

export function useApp(): Ctx {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp outside AppProvider");
  return ctx;
}

export function AppProvider({ children }: { children: ReactNode }) {
  const value = useAppState();
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

function useAppState() {
  // ---- preferences
  const [prefs, setPrefsState] = useState<Prefs>(() => readStore("odp.prefs", DEFAULT_PREFS));
  const setPrefs = useCallback((patch: Partial<Prefs>) => setPrefsState((p) => {
    const next = { ...p, ...patch };
    writeStore("odp.prefs", next);
    return next;
  }), []);
  useEffect(() => {
    const apply = () => {
      document.documentElement.dataset.theme = resolvedTheme(prefs.theme);
      document.documentElement.dataset.density = prefs.density;
    };
    apply();
    const mq = window.matchMedia?.("(prefers-color-scheme: light)");
    mq?.addEventListener("change", apply);
    return () => mq?.removeEventListener("change", apply);
  }, [prefs.theme, prefs.density]);

  // ---- toasts
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toastId = useRef(0);
  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const notify = useCallback((tone: Toast["tone"], message: string) => {
    if (!message) return;
    const id = ++toastId.current;
    setToasts((t) => (t.some((x) => x.message === message) ? t : [...t.slice(-3), { id, tone, message }]));
    setTimeout(() => dismiss(id), tone === "bad" ? 12000 : 5000);
  }, [dismiss]);
  /** Error reporter handed to every panel. */
  const onError = useCallback((message: string) => notify("bad", message), [notify]);

  // ---- navigation
  const [route, setRoute] = useState<Route>(() => readStore<{ r: Route }>("odp.route", { r: { view: "home" } }).r);
  const history = useRef<Route[]>([]);
  const nav = useCallback((next: Route) => {
    setRoute((cur) => {
      if (cur.view !== next.view) setDrawer(false);
      if (JSON.stringify(cur) !== JSON.stringify(next)) history.current = [...history.current.slice(-30), cur];
      writeStore("odp.route", { r: next });
      return next;
    });
  }, []);
  const back = useCallback(() => {
    const prev = history.current.pop();
    if (prev) { setRoute(prev); writeStore("odp.route", { r: prev }); }
  }, []);

  // ---- dialogs
  const [dialog, setDialog] = useState<DialogSpec | null>(null);
  const [password, setPassword] = useState<PasswordRequest | null>(null);
  const [confirmReq, setConfirmReq] = useState<ConfirmRequest | null>(null);
  const confirm = useCallback((req: Omit<ConfirmRequest, "resolve">) => new Promise<boolean>((resolve) => setConfirmReq({ ...req, resolve })), []);
  const [palette, setPalette] = useState(false);
  /** Below 1120 px the inspector is a drawer over the page: closed until something is selected or it is toggled. */
  const [drawer, setDrawer] = useState(false);

  /** Data version per area; views reload when theirs changes. */
  const [versions, setVersions] = useState<Record<string, number>>({});
  const invalidate = useCallback((...keys: string[]) => setVersions((v) => {
    const next = { ...v };
    for (const k of keys) next[k] = (next[k] ?? 0) + 1;
    return next;
  }), []);

  // ---- core state
  const [info, setInfo] = useState<AppInfo | null>(null);
  const [coreDown, setCoreDown] = useState(false);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [scanning, setScanning] = useState(false);
  const [doctor, setDoctor] = useState<{ report: DoctorReport | null; running: boolean; at: string | null }>({ report: null, running: false, at: null });

  const refresh = useCallback(async () => {
    try {
      const [a, s] = await Promise.all([rpc.request<Agent[]>("agents.list"), rpc.request<Session[]>("sessions.list")]);
      setAgents(a);
      setSessions(s);
    } catch (e) {
      onError(String((e as Error).message));
    }
  }, [onError]);

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

  const runDoctor = useCallback(async () => {
    setDoctor((d) => ({ ...d, running: true }));
    try {
      const report = await rpc.request<DoctorReport>("doctor.run");
      setDoctor({ report, running: false, at: new Date().toISOString() });
    } catch (e) {
      onError(String((e as Error).message));
      setDoctor((d) => ({ ...d, running: false }));
    }
  }, [onError]);

  /** Run an action with a status-bar label; errors become toasts. */
  const act = useCallback(async (label: string, fn: () => Promise<unknown>, ok?: string) => {
    setBusy(label);
    try {
      await fn();
      if (ok) notify("ok", ok);
      return true;
    } catch (e) {
      onError(String((e as Error).message));
      return false;
    } finally {
      setBusy(null);
      refresh();
    }
  }, [notify, onError, refresh]);

  // ---- followed session (the output dock and the Sessions view)
  const [followed, setFollowed] = useState<Session | null>(null);
  const [log, setLog] = useState("");
  const followRef = useRef<{ user: string; id: string; offset: number } | null>(null);

  const follow = useCallback(async (session: Session) => {
    const previous = followRef.current;
    if (previous && previous.user === session.user && previous.id === session.id) return;
    if (previous) rpc.request("session.unfollow", { user: previous.user, id: previous.id }).catch(() => undefined);
    setFollowed(session);
    setLog("");
    // Start near the end of long logs.
    const offset = Math.max(0, session.log_size - LOG_LIMIT);
    followRef.current = { user: session.user, id: session.id, offset };
    await rpc.request("session.follow", { user: session.user, id: session.id, offset }).catch((e) => onError(e.message));
  }, [onError]);

  const followedLive = followed ? sessions.find((s) => s.user === followed.user && s.id === followed.id) ?? followed : null;

  // ---- startup and core events
  useEffect(() => {
    rpc.handle(
      "ui.askPassword",
      (params) => new Promise((resolve) =>
        setPassword({ prompt: params.prompt, user: params.user, purpose: params.purpose, resolve: (pw) => resolve({ password: pw }) })),
    );
    const offs = [
      rpc.on("session.output", (p) => {
        const f = followRef.current;
        if (!f || f.id !== p.id || f.user !== p.user) return;
        f.offset = p.offset;
        setLog((old) => (old + p.data.replace(ANSI, "")).slice(-LOG_LIMIT));
      }),
      rpc.on("session.state", () => refresh()),
      rpc.on("session.ended", () => refresh()),
      rpc.on("agent.disconnected", () => refresh()),
      rpc.on("sidecar.exit", () => { setCoreDown(true); onError("The core process exited. Restart the app."); }),
    ];
    rpc.request<AppInfo>("app.info").then(setInfo).catch((e) => onError(String(e.message)));
    refresh();
    scan();
    const timer = setInterval(refresh, 3000);
    return () => { offs.forEach((off) => off()); clearInterval(timer); };
  }, [refresh, scan, onError]);

  // Re-follow after an agent comes back (for example after "Unlock" following an agent crash).
  const agentKey = agents.map((a) => `${a.user}:${a.state}:${a.info?.pid}`).join(",");
  useEffect(() => {
    const f = followRef.current;
    if (!f) return;
    if (agents.find((a) => a.user === f.user)?.state === "running") {
      rpc.request("session.follow", { user: f.user, id: f.id, offset: f.offset }).catch(() => undefined);
    }
  }, [agentKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const joinGroup = useCallback(async () => {
    await act("joining the odoo-dev group", async () => {
      await rpc.request("group.join");
      await rpc.restart();
      setInfo(await rpc.request("app.info"));
    });
  }, [act]);

  // ---- derived lookups
  const runningAgents = useMemo(() => new Set(agents.filter((a) => a.state === "running").map((a) => a.user)), [agents]);
  const ownerOf = useCallback((instancePath: string) => {
    const inst = snap?.instances.find((i) => i.path === instancePath);
    return snap?.installations.find((x) => x.root === inst?.installation)?.owner ?? null;
  }, [snap]);

  /** Live state of an instance: a running session the app started, or a process found by the last scan. */
  const instanceState = useCallback((path: string) => {
    const session = sessions.find((s) => s.state === "running" && s.meta?.instance === path && s.meta?.kind !== "shell");
    if (session) return { running: true, port: session.meta?.port ?? null, session, pid: session.pid, external: false };
    const proc = snap?.processes.find((p) => p.instance === path);
    if (proc) return { running: true, port: proc.port, session: null, pid: proc.pid, external: true, unit: proc.unit ?? null };
    return { running: false, port: null, session: null, pid: null, external: false };
  }, [sessions, snap]);

  const startSession = useCallback(async (user: string, id: string) => {
    await refresh();
    const s = (await rpc.request<Session[]>("sessions.list")).find((x) => x.user === user && x.id === id);
    if (s) {
      await follow(s);
      setPrefs({ dock: true });
    }
  }, [refresh, follow, setPrefs]);

  /** Start an instance with its config's defaults (the same call as Start in the Run form). */
  const quickStart = useCallback(async (path: string) => {
    const owner = ownerOf(path);
    if (!owner) return onError("This instance has no installation with a venv and a run-as user.");
    if (!runningAgents.has(owner)) return onError(`Unlock the ${owner} agent first (Sessions → Agents).`);
    await act("starting Odoo", async () => {
      const r = await rpc.request<{ id: string }>("run.start", { instance: path, update: [], install: [], stop_after_init: false, dev: [] });
      await startSession(owner, r.id);
    });
  }, [ownerOf, runningAgents, act, onError, startSession]);

  const stopSession = useCallback((s: Session) => act(`stopping ${s.name}`, () => rpc.request("session.stop", { user: s.user, id: s.id })), [act]);
  const openPort = useCallback((port: number) => { rpc.request("run.open", { port }).catch((e) => onError(String(e.message))); }, [onError]);

  return {
    prefs, setPrefs,
    toasts, notify, dismiss, onError,
    route, nav, back,
    drawer, setDrawer, inspect: () => { setPrefs({ inspector: true }); setDrawer(true); },
    dialog, setDialog, password, setPassword, confirmReq, setConfirmReq, confirm, palette, setPalette,
    versions, invalidate,
    info, coreDown, agents, sessions, busy, refresh, act, joinGroup,
    snap, scanning, scan,
    doctor, runDoctor,
    followed: followedLive, log, follow,
    runningAgents, ownerOf, instanceState, startSession, quickStart, stopSession, openPort,
  };
}
