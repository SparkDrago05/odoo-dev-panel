import { Activity, ExternalLink, KeyRound, Lock, Play, Power, Square, TerminalSquare, UserPlus } from "lucide-react";
import { useEffect, useState } from "react";
import { rpc } from "../rpc";
import { sessionKey, useApp } from "../state/app";
import type { Agent, Session } from "../types";
import { LogConsole } from "../ui/LogConsole";
import { ago, Badge, Cmd, Dot, duration, EmptyState, KV, listKeys, Segmented } from "../ui/primitives";
import { Problems } from "./Problems";

function AgentCard({ a }: { a: Agent }) {
  const app = useApp();
  const running = a.state === "running";
  return (
    <div className="agent-card">
      <Dot tone={running ? "ok" : a.state === "error" ? "bad" : "idle"} />
      <div className="who">
        <span className="n">{a.user}</span>
        <span className="s" title={a.error}>
          {running ? `unlocked · pid ${a.info?.pid} · ${a.info?.running_sessions ?? 0} running` : !a.enabled ? "not in odoo-dev" : a.state === "error" ? a.error ?? "error" : "locked"}
        </span>
      </div>
      {!a.enabled && !running ? (
        <button className="btn sm" title={`Runs: sudo usermod -aG odoo-dev ${a.user}`} onClick={() => app.act(`enabling ${a.user}`, () => rpc.request("agent.enable", { user: a.user }))}>
          <UserPlus />Enable
        </button>
      ) : running ? (
        <button className="btn ghost sm" title="Stop the agent; running Odoo keeps running" onClick={() => app.act(`stopping agent ${a.user}`, () => rpc.request("agent.stop", { user: a.user }))}>
          <Power />Lock
        </button>
      ) : (
        <button className="btn primary sm" onClick={() => app.act(`unlocking ${a.user}`, () => rpc.request("agent.start", { user: a.user }))}><KeyRound />Unlock</button>
      )}
    </div>
  );
}

function SessionRow({ s, selected, onSelect }: { s: Session; selected: boolean; onSelect: () => void }) {
  const running = s.state === "running" || s.state === "stopping";
  return (
    <div className="session-card" role="option" aria-selected={selected} tabIndex={selected ? 0 : -1} onClick={onSelect}>
      <div className="top">
        <Dot tone={running ? (s.state === "stopping" ? "warn" : "ok") : s.state === "lost" ? "bad" : s.exit_code ? "bad" : "idle"} live={s.state === "running"} />
        <span className="name">{s.name}</span>
        {s.meta?.port && running && <Badge mono tone="ok">:{s.meta.port}</Badge>}
        {s.pty && <Badge><TerminalSquare />shell</Badge>}
      </div>
      <div className="bottom">
        <span>{s.user}</span>
        {s.meta?.db && <span>{s.meta.db}</span>}
        <span>{running ? duration(s.started_at) : `exit ${s.exit_code ?? "?"} · ${ago(s.ended_at ?? s.started_at)}`}</span>
        {s.adopted && <span>re-adopted</span>}
      </div>
    </div>
  );
}

function ShellInput({ session }: { session: Session }) {
  const app = useApp();
  const [line, setLine] = useState("");
  const [history, setHistory] = useState<string[]>([]);
  const [pos, setPos] = useState(-1);
  const send = (data: string) => rpc.request("session.write", { user: session.user, id: session.id, data }).catch((e) => app.onError(String(e.message)));
  if (session.state !== "running") return <div className="shell-line"><span className="muted small">Shell ended.</span></div>;
  return (
    <form className="shell-line" onSubmit={(e) => {
      e.preventDefault();
      send(line + "\n");
      if (line.trim()) setHistory([line, ...history].slice(0, 200));
      setLine(""); setPos(-1);
    }}>
      <span className="mono small" style={{ color: "var(--accent-text)", alignSelf: "center" }}>&gt;&gt;&gt;</span>
      <input autoFocus value={line} aria-label="Shell input" spellCheck={false}
        placeholder="Python; env is the Odoo environment. Enter sends, Ctrl+C interrupts, Ctrl+D exits."
        onChange={(e) => setLine(e.target.value)}
        onKeyDown={(e) => {
          if (e.ctrlKey && (e.key === "c" || e.key === "d") && !line) { e.preventDefault(); send(e.key === "c" ? "\x03" : "\x04"); }
          else if (e.key === "ArrowUp" && history.length) { e.preventDefault(); const n = Math.min(pos + 1, history.length - 1); setPos(n); setLine(history[n]); }
          else if (e.key === "ArrowDown") { e.preventDefault(); const n = pos - 1; setPos(Math.max(n, -1)); setLine(n >= 0 ? history[n] : ""); }
        }} />
      <button className="btn sm" type="submit">Send</button>
    </form>
  );
}

function SessionDetail({ s }: { s: Session }) {
  const app = useApp();
  const [view, setView] = useState<"output" | "problems">("output");
  const running = s.state === "running";
  const instance = s.meta?.instance ? app.snap?.instances.find((i) => i.path === s.meta!.instance) : undefined;
  useEffect(() => setView("output"), [s.id]);
  return (
    <div style={{ display: "grid", gridTemplateRows: "auto 1fr", height: "100%", minHeight: 0 }}>
      <div className="stack" style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)" }}>
        <div className="row nowrap">
          <Dot tone={running ? "ok" : s.exit_code ? "bad" : "idle"} live={running} />
          <h2 className="truncate" style={{ fontSize: 16 }}>{s.name}</h2>
          <Badge>{s.state}{s.state === "exited" ? ` · exit ${s.exit_code ?? "unknown"}` : ""}</Badge>
          {s.meta?.kind && <Badge>{s.meta.kind}</Badge>}
          <span className="grow" />
          {running && s.meta?.port && <button className="btn sm" onClick={() => app.openPort(s.meta!.port!)}><ExternalLink />Open</button>}
          {(running || s.state === "stopping") && <button className="btn sm" onClick={() => app.stopSession(s)}><Square />Stop</button>}
          {!running && instance && <button className="btn sm" onClick={() => app.setDialog({ kind: "run", instance: instance.path })}><Play />Run again…</button>}
        </div>
        <KV items={[
          ["Run as", s.user, "mono"],
          ["Process", `pid ${s.pid}${s.adopted ? " · re-adopted after an app restart" : ""}`, "mono"],
          ["Started", `${new Date(s.started_at).toLocaleString()} · ${duration(s.started_at, s.ended_at)}${running ? "" : " long"}`],
          ["Instance", instance ? <a onClick={() => app.nav({ view: "instance", path: instance.path })}>{instance.name}</a> : s.meta?.instance ?? null],
          ["Database", s.meta?.db ?? null, "mono"],
        ]} />
        <Cmd>{s.argv.join(" ")}</Cmd>
      </div>
      <div style={{ display: "grid", gridTemplateRows: "1fr auto", minHeight: 0 }}>
        {view === "problems" && !s.pty ? (
          <div style={{ overflow: "auto", minHeight: 0 }}>
            <div className="term-bar"><Segmented label="View" value={view} onChange={setView} options={[{ value: "output", label: "Output" }, { value: "problems", label: "Problems" }]} /></div>
            <Problems user={s.user} id={s.id} state={s.state} instance={s.meta?.instance} />
          </div>
        ) : (
          <LogConsole text={app.log} levels={!s.pty} empty={running ? "Waiting for output…" : "No output."}
            toolbar={!s.pty && <Segmented label="View" value={view} onChange={setView} options={[{ value: "output", label: "Output" }, { value: "problems", label: "Problems" }]} />} />
        )}
        {s.pty && <ShellInput session={s} />}
      </div>
    </div>
  );
}

/** Agents, running sessions, history and the selected session's output. */
export function SessionsView({ selectedKey }: { selectedKey?: string }) {
  const app = useApp();
  const { sessions, agents, followed } = app;
  const running = sessions.filter((s) => s.state === "running" || s.state === "stopping");
  const past = sessions.filter((s) => !(s.state === "running" || s.state === "stopping"));
  const key = selectedKey ?? (followed ? sessionKey(followed) : running[0] ? sessionKey(running[0]) : undefined);
  const selected = sessions.find((s) => sessionKey(s) === key) ?? null;
  useEffect(() => { if (selected) app.follow(selected); }, [selected?.id, selected?.user]); // eslint-disable-line react-hooks/exhaustive-deps
  const pick = (s: Session) => app.nav({ view: "sessions", key: sessionKey(s) });
  const ordered = [...running, ...past];
  const locked = agents.filter((a) => a.state !== "running");

  return (
    <div className="split sessions" style={{ flex: 1 }}>
      <div className="pane" style={{ background: "var(--surface)" }}>
        <div className="stack" style={{ padding: 14 }}>
          <div className="row between">
            <span className="section-title">Agents</span>
            {locked.length > 0 && <span className="xs dim"><Lock style={{ width: 11, height: 11, verticalAlign: -1 }} /> {locked.length} locked</span>}
          </div>
          {agents.length === 0 ? <p className="muted small">No version users found. Add them to the odoo-dev group.</p>
            : <div className="stack tight">{agents.map((a) => <AgentCard key={a.user} a={a} />)}</div>}
          <button className="btn primary" onClick={() => app.setDialog({ kind: "run" })}><Play />Run Odoo…</button>
        </div>
        <div role="listbox" aria-label="Sessions" onKeyDown={listKeys(ordered, selected, (a, b) => sessionKey(a) === sessionKey(b), pick)}>
          <div className="list-group-head" style={{ cursor: "default" }}><Activity />Running<span className="grow" /><span className="count-pill ok">{running.length}</span></div>
          {running.length === 0 && <p className="muted small" style={{ padding: "10px 14px" }}>Nothing runs right now.</p>}
          {running.map((s) => <SessionRow key={sessionKey(s)} s={s} selected={key === sessionKey(s)} onSelect={() => pick(s)} />)}
          <div className="list-group-head" style={{ cursor: "default" }}>History<span className="grow" /><span className="count-pill">{past.length}</span></div>
          {past.map((s) => <SessionRow key={sessionKey(s)} s={s} selected={key === sessionKey(s)} onSelect={() => pick(s)} />)}
        </div>
      </div>
      <div className="pane" style={{ overflow: "hidden" }}>
        {selected ? <SessionDetail s={selected} /> : (
          <EmptyState icon={<Activity />} title="No session selected" actions={<button className="btn primary" onClick={() => app.setDialog({ kind: "run" })}><Play />Run Odoo…</button>}>
            Sessions are Odoo processes started from here. They keep running when the app closes, and their output is kept.
          </EmptyState>
        )}
      </div>
    </div>
  );
}
