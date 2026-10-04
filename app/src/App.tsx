import { useCallback, useEffect, useRef, useState } from "react";
import Discover from "./Discover";
import Databases from "./Databases";
import Doctor from "./Doctor";
import Provision from "./Provision";
import Run from "./Run";
import { rpc } from "./rpc";

type Agent = {
  user: string;
  state: "running" | "stopped" | "error";
  enabled: boolean;
  info?: { pid: number; started_at: string; running_sessions: number };
  error?: string;
};

type Session = {
  id: string;
  user: string;
  name: string;
  argv: string[];
  pid: number;
  state: string;
  started_at: string;
  ended_at: string | null;
  exit_code: number | null;
  adopted: boolean;
  log_size: number;
  pty?: boolean;
  meta?: { kind?: string; db?: string | null; port?: number | null; instance?: string };
};

type PasswordRequest = { prompt: string; user: string | null; purpose?: string; resolve: (password: string | null) => void };

const LOG_LIMIT = 400_000;
// Terminal control sequences and carriage returns from PTY sessions; the log pane shows plain text.
const ANSI = /\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r/g;

export default function App() {
  const [info, setInfo] = useState<{ version: string; user: string } | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selected, setSelected] = useState<Session | null>(null);
  const [log, setLog] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [showProvision, setShowProvision] = useState(false);
  const [passwordRequest, setPasswordRequest] = useState<PasswordRequest | null>(null);
  const followRef = useRef<{ user: string; id: string; offset: number } | null>(null);
  const logRef = useRef<HTMLPreElement>(null);

  const refresh = useCallback(async () => {
    try {
      const [a, s] = await Promise.all([rpc.request<Agent[]>("agents.list"), rpc.request<Session[]>("sessions.list")]);
      setAgents(a);
      setSessions(s);
    } catch (e) {
      setError(String((e as Error).message));
    }
  }, []);

  // Sidecar events.
  useEffect(() => {
    rpc.handle(
      "ui.askPassword",
      (params) =>
        new Promise((resolve) =>
          setPasswordRequest({ prompt: params.prompt, user: params.user, purpose: params.purpose, resolve: (password) => resolve({ password }) }),
        ),
    );
    const offs = [
      rpc.on("session.output", (p) => {
        const follow = followRef.current;
        if (!follow || follow.id !== p.id || follow.user !== p.user) return;
        follow.offset = p.offset;
        setLog((old) => (old + p.data.replace(ANSI, "")).slice(-LOG_LIMIT));
      }),
      rpc.on("session.state", () => refresh()),
      rpc.on("session.ended", () => refresh()),
      rpc.on("agent.disconnected", () => refresh()),
      rpc.on("sidecar.exit", () => setError("The core process exited. Restart the app.")),
    ];
    rpc.request("app.info").then(setInfo).catch((e) => setError(String(e.message)));
    refresh();
    const timer = setInterval(refresh, 3000);
    return () => {
      offs.forEach((off) => off());
      clearInterval(timer);
    };
  }, [refresh]);

  // Re-follow after an agent comes back (for example after "Unlock" following an agent crash).
  useEffect(() => {
    const follow = followRef.current;
    if (!follow) return;
    const agent = agents.find((a) => a.user === follow.user);
    if (agent?.state === "running") {
      rpc.request("session.follow", { user: follow.user, id: follow.id, offset: follow.offset }).catch(() => undefined);
    }
  }, [agents.map((a) => `${a.user}:${a.state}:${a.info?.pid}`).join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const el = logRef.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 80) el.scrollTop = el.scrollHeight;
  }, [log]);

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy(null);
      refresh();
    }
  };

  const select = async (session: Session) => {
    const previous = followRef.current;
    if (previous) rpc.request("session.unfollow", { user: previous.user, id: previous.id }).catch(() => undefined);
    setSelected(session);
    setLog("");
    // Start near the end of long logs.
    const offset = Math.max(0, session.log_size - LOG_LIMIT);
    followRef.current = { user: session.user, id: session.id, offset };
    await rpc.request("session.follow", { user: session.user, id: session.id, offset }).catch((e) => setError(e.message));
  };

  const runningAgents = agents.filter((a) => a.state === "running");

  return (
    <div className="layout">
      <header>
        <h1>Odoo Dev Panel</h1>
        <span className="muted">
          {info ? `v${info.version} · ${info.user}` : "starting core…"} {busy && `· ${busy}…`}
        </span>
        <button className="primary" style={{ marginLeft: "auto" }} onClick={() => setShowProvision(true)}>New installation</button>
      </header>

      {error && (
        <div className="error" role="alert">
          {error} <button onClick={() => setError(null)}>Dismiss</button>
        </div>
      )}

      <Discover onError={setError} />

      <Doctor onError={setError} />

      <Databases onError={setError} />

      <section>
        <h2>Agents</h2>
        {agents.length === 0 && <p className="muted">No version users found. Add them to the odoo-dev group.</p>}
        <table>
          <tbody>
            {agents.map((a) => (
              <tr key={a.user}>
                <td>{a.user}</td>
                <td>
                  <span className={`dot ${a.state}`} /> {a.state}
                  {a.info && <span className="muted"> · pid {a.info.pid} · since {a.info.started_at}</span>}
                  {!a.enabled && a.state !== "running" && (
                    <span className="muted"> · not in odoo-dev; Enable runs <code>sudo usermod -aG odoo-dev {a.user}</code></span>
                  )}
                </td>
                <td className="actions">
                  {!a.enabled && a.state !== "running" ? (
                    <button
                      className="primary"
                      title={`Runs: sudo usermod -aG odoo-dev ${a.user}`}
                      onClick={() => run(`enabling ${a.user}`, () => rpc.request("agent.enable", { user: a.user }))}
                    >
                      Enable
                    </button>
                  ) : a.state === "running" ? (
                    <button onClick={() => run(`stopping agent ${a.user}`, () => rpc.request("agent.stop", { user: a.user }))}>
                      Stop agent
                    </button>
                  ) : (
                    <button
                      className="primary"
                      onClick={() => run(`unlocking ${a.user}`, () => rpc.request("agent.start", { user: a.user }))}
                    >
                      Unlock
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <Run
        agents={runningAgents.map((a) => a.user)}
        onError={setError}
        onStarted={async (user, id) => {
          await refresh();
          const session = (await rpc.request<Session[]>("sessions.list")).find((x) => x.user === user && x.id === id);
          if (session) await select(session);
        }}
      />

      <section>
        <h2>Sessions</h2>
        {sessions.length === 0 && <p className="muted">No sessions.</p>}
        <table>
          <thead>
            <tr><th>Name</th><th>User</th><th>State</th><th>PID</th><th>Started</th><th /></tr>
          </thead>
          <tbody>
            {sessions.map((s) => (
              <tr key={`${s.user}/${s.id}`} className={selected?.id === s.id ? "selected" : ""} onClick={() => select(s)}>
                <td>{s.name}</td>
                <td>{s.user}</td>
                <td>
                  <span className={`dot ${s.state}`} /> {s.state}
                  {s.adopted && <span className="muted"> · re-adopted</span>}
                  {s.state === "exited" && <span className="muted"> · exit {s.exit_code ?? "unknown"}</span>}
                  {s.meta?.kind && <span className="muted"> · {s.meta.kind}{s.meta.port ? ` :${s.meta.port}` : ""}</span>}
                </td>
                <td>{s.pid}</td>
                <td>{s.started_at}</td>
                <td className="actions">
                  {s.state === "running" && s.meta?.port && (
                    <button onClick={(e) => {
                      e.stopPropagation();
                      rpc.request("run.open", { port: s.meta!.port }).catch((err) => setError(String(err.message)));
                    }}>Open</button>
                  )}
                  {(s.state === "running" || s.state === "stopping") && (
                    <button onClick={(e) => {
                      e.stopPropagation();
                      run(`stopping ${s.name}`, () => rpc.request("session.stop", { user: s.user, id: s.id }));
                    }}>Stop</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="log">
        <h2>Output {selected && <span className="muted">· {selected.name} ({selected.id})</span>}</h2>
        {selected && <code className="command">{selected.argv.join(" ")}</code>}
        <pre ref={logRef}>{selected ? log : "Select a session."}</pre>
        {selected?.pty && (
          <ShellInput
            session={sessions.find((x) => x.user === selected.user && x.id === selected.id) ?? selected}
            onError={setError}
          />
        )}
      </section>

      {showProvision && <Provision onClose={() => { setShowProvision(false); refresh(); }} />}

      {passwordRequest && (
        <PasswordDialog
          request={passwordRequest}
          onDone={(password) => {
            passwordRequest.resolve(password);
            setPasswordRequest(null);
          }}
        />
      )}
    </div>
  );
}

function ShellInput({ session, onError }: { session: Session; onError: (message: string) => void }) {
  const [line, setLine] = useState("");
  const [history, setHistory] = useState<string[]>([]);
  const [pos, setPos] = useState(-1);
  const send = (data: string) =>
    rpc.request("session.write", { user: session.user, id: session.id, data }).catch((e) => onError(String(e.message)));
  if (session.state !== "running") return <p className="muted">Shell ended.</p>;
  return (
    <form
      className="row"
      onSubmit={(e) => {
        e.preventDefault();
        send(line + "\n");
        if (line.trim()) setHistory([line, ...history].slice(0, 200));
        setLine("");
        setPos(-1);
      }}
    >
      <input
        className="shell-input"
        autoFocus
        value={line}
        placeholder=">>> Python; env is the Odoo environment. Enter sends, Ctrl-C interrupts, Ctrl-D exits."
        aria-label="Shell input"
        onChange={(e) => setLine(e.target.value)}
        onKeyDown={(e) => {
          if (e.ctrlKey && (e.key === "c" || e.key === "d") && !line) {
            e.preventDefault();
            send(e.key === "c" ? "\x03" : "\x04");
          } else if (e.key === "ArrowUp" && history.length) {
            e.preventDefault();
            const next = Math.min(pos + 1, history.length - 1);
            setPos(next);
            setLine(history[next]);
          } else if (e.key === "ArrowDown") {
            e.preventDefault();
            const next = pos - 1;
            setPos(Math.max(next, -1));
            setLine(next >= 0 ? history[next] : "");
          }
        }}
      />
      <button type="submit">Send</button>
    </form>
  );
}

function PasswordDialog({ request, onDone }: { request: PasswordRequest; onDone: (password: string | null) => void }) {
  const [password, setPassword] = useState("");
  return (
    <div className="modal-backdrop">
      <form
        className="modal"
        onSubmit={(e) => {
          e.preventDefault();
          onDone(password);
        }}
      >
        <h2>{request.purpose === "provision" ? "Create installation" : request.purpose === "enable" ? `Enable ${request.user}` : `Unlock ${request.user ?? "agent"}`}</h2>
        <p className="muted">
          {request.purpose === "provision"
            ? `sudo asks for your password once, to run the reviewed script that creates ${request.user}. `
            : request.purpose === "enable"
            ? `sudo asks for your password to run: usermod -aG odoo-dev ${request.user}. `
            : `sudo asks for your password to start the agent as ${request.user}. `}
          It is passed to sudo and not stored.
        </p>
        <label>
          {request.prompt || "Password:"}
          <input type="password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        <div className="row end">
          <button type="button" onClick={() => onDone(null)}>Cancel</button>
          <button className="primary" type="submit">{request.purpose === "unlock" || !request.purpose ? "Unlock" : "Continue"}</button>
        </div>
      </form>
    </div>
  );
}
