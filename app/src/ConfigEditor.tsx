import { useEffect, useRef, useState } from "react";
import { type Changes, ConfigForm, type FormData } from "./ConfigForm";
import { rpc } from "./rpc";

type Issue = { level: "error" | "warning"; key: string | null; text: string };
type Access = { owner: string; group: string; mode: string; writable: boolean; others_read: boolean };
type Opened = { path: string; text: string; sha: string; access: Access; issues: Issue[] };
type Form = FormData & { text: string; issues: Issue[] };
type Change = { path: string; kind: string; current: string; target: string };
type PermsPlan = { root: string; run_as: string; dev_user: string; changes: Change[]; kept: string[]; notes: string[]; script: string };
type StepEvent = { run_id: string; step: string; status: "start" | "output" | "ok" | "fail"; text: string };
type Finished = { run_id: string; ok: boolean; error: string | null; changed?: number; receipt?: string | null };

const MASK = "********";

export function ConfigEditor({ path, root, onClose }: { path: string; root: string | null; onClose: (changed: boolean) => void }) {
  const [current, setCurrent] = useState(path);
  const [opened, setOpened] = useState<Opened | null>(null);
  const [text, setText] = useState("");
  const [issues, setIssues] = useState<Issue[]>([]);
  const [revealed, setRevealed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [changed, setChanged] = useState(false);
  const [copyName, setCopyName] = useState("");
  const [fixing, setFixing] = useState(false);
  const [view, setView] = useState<"form" | "raw">("form");
  const [form, setForm] = useState<FormData | null>(null);
  const textRef = useRef("");
  textRef.current = text;
  const formText = useRef<string | null>(null);
  const pending = useRef<Promise<void>>(Promise.resolve());

  const load = async (p: string, reveal: boolean) => {
    setError(null);
    try {
      const o = await rpc.request<Opened>("config.open", { path: p, reveal });
      formText.current = null;
      setOpened(o); setText(o.text); setIssues(o.issues); setRevealed(reveal);
    } catch (e) {
      setError(String((e as Error).message));
    }
  };
  useEffect(() => { load(current, false); }, [current]);

  const dirty = opened !== null && text !== opened.text;
  // The open result's issues include the file mode check; an edited text gets the issues of config.form.
  const showForm = (r: Form) => {
    formText.current = r.text;
    setForm(r);
    if (opened && r.text !== opened.text) setIssues(r.issues);
    else if (opened) setIssues(opened.issues);
  };
  useEffect(() => {
    if (!opened || formText.current === text) return;
    const timer = setTimeout(() => {
      rpc.request<Form>("config.form", { path: current, text })
        .then((r) => { if (r.text === textRef.current) showForm(r); }).catch(() => undefined);
    }, form ? 400 : 0);
    return () => clearTimeout(timer);
  }, [text, opened]);

  // Save waits for a form change still on its way (a field commits when it loses focus).
  const apply = (changes: Changes) => {
    setError(null); setNotice(null);
    pending.current = pending.current.then(async () => {
      try {
        const r = await rpc.request<Form>("config.form", { path: current, text: textRef.current, changes });
        textRef.current = r.text;
        setText(r.text);
        showForm(r);
      } catch (e) {
        setError(String((e as Error).message));
      }
    });
  };

  const errors = issues.filter((i) => i.level === "error");
  const readOnly = opened !== null && !opened.access.writable;

  const save = async () => {
    if (!opened) return;
    setSaving(true); setError(null); setNotice(null);
    try {
      await pending.current;
      const r = await rpc.request<{ changed: boolean; backup: string | null }>("config.save", { path: current, text: textRef.current, sha: opened.sha });
      setNotice(r.changed ? `Saved. The previous version is in ${r.backup}.` : "No change.");
      if (r.changed) setChanged(true);
      await load(current, revealed);
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setSaving(false);
    }
  };

  const copy = async () => {
    setError(null); setNotice(null);
    try {
      const r = await rpc.request<{ path: string; warning: string | null }>("config.copy", { path: current, name: copyName });
      setChanged(true); setCopyName("");
      setNotice(r.warning ? `Created ${r.path}. ${r.warning}` : `Created ${r.path}.`);
      setCurrent(r.path);
    } catch (e) {
      setError(String((e as Error).message));
    }
  };

  const toggleReveal = () => {
    if (dirty && !confirm("Discard your unsaved changes?")) return;
    load(current, !revealed);
  };

  return (
    <div className="modal-backdrop">
      <div className="modal wide">
        <div className="row between">
          <h2>{current}</h2>
          <button disabled={saving} onClick={() => { if (!dirty || confirm("Discard your unsaved changes?")) onClose(changed); }}>Close</button>
        </div>
        {error && <div className="error" role="alert">{error}</div>}
        {notice && <p>{notice}</p>}
        {opened && (
          <>
            <p className="muted">
              {opened.access.owner}:{opened.access.group} {opened.access.mode}
              {readOnly ? " · read-only for you" : ""}
              {opened.access.others_read ? " · every local user can read it" : ""}
            </p>
            {readOnly && root && (
              <div className="row">
                <span className="muted">You cannot save this file. Fix permissions makes you the owner and the run-as user's group the reader (one sudo prompt).</span>
                <button onClick={() => setFixing(true)}>Fix permissions</button>
              </div>
            )}
            <div className="row">
              <button className={view === "form" ? "primary" : ""} onClick={() => setView("form")}>Form</button>
              <button className={view === "raw" ? "primary" : ""} onClick={() => setView("raw")}>Raw</button>
            </div>
            {view === "form" ? (
              form ? <ConfigForm data={form} readOnly={readOnly} revealed={revealed} apply={apply} /> : <p className="muted">Loading…</p>
            ) : (
              <textarea className="config" value={text} readOnly={readOnly} spellCheck={false}
                onChange={(e) => setText(e.target.value)} rows={22} />
            )}
            <div className="row between">
              <span className="muted">Passwords show as {MASK}; leave them to keep the current value, or type a new one.</span>
              <button onClick={toggleReveal}>{revealed ? "Hide passwords" : "Reveal passwords"}</button>
            </div>
            {issues.length > 0 && (
              <ul>
                {issues.map((i, n) => (
                  <li key={n}><span className={`dot ${i.level === "error" ? "error" : "stopping"}`} /> {i.key ? <code>{i.key}</code> : null} {i.text}</li>
                ))}
              </ul>
            )}
            <div className="row between">
              <span>
                <input value={copyName} placeholder="new config name" onChange={(e) => setCopyName(e.target.value)} />{" "}
                <button disabled={!copyName || dirty} title={dirty ? "Save or discard your changes first" : ""} onClick={copy}>
                  Copy to new config
                </button>
              </span>
              <button className="primary" disabled={readOnly || !dirty || errors.length > 0 || saving} onClick={save}>
                {errors.length > 0 ? "Fix the errors first" : saving ? "Saving…" : "Save"}
              </button>
            </div>
          </>
        )}
      </div>
      {fixing && root && (
        <FixPermissions root={root} onClose={(applied) => { setFixing(false); if (applied) { setChanged(true); load(current, revealed); } }} />
      )}
    </div>
  );
}

export function FixPermissions({ root, onClose }: { root: string; onClose: (applied: boolean) => void }) {
  const [plan, setPlan] = useState<PermsPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [log, setLog] = useState("");
  const [started, setStarted] = useState(false);
  const [finished, setFinished] = useState<Finished | null>(null);
  const runId = useRef<string | null>(null);

  useEffect(() => {
    rpc.request<PermsPlan>("perms.plan", { root }).then(setPlan).catch((e) => setError(String(e.message)));
    const offs = [
      rpc.on("repair.step", (e: StepEvent) => {
        if (e.run_id !== runId.current) return;
        const line = e.status === "start" ? `== ${e.step}: ${e.text}` : e.status === "output" ? e.text
          : `== ${e.step}: ${e.status === "ok" ? "ok" : `FAILED ${e.text}`}`;
        setLog((old) => old + line + "\n");
      }),
      rpc.on("repair.finished", (e: Finished) => { if (e.run_id === runId.current) setFinished(e); }),
    ];
    return () => offs.forEach((off) => off());
  }, [root]);

  const apply = async () => {
    setError(null);
    try {
      const r = await rpc.request<{ run_id: string }>("perms.run", { root });
      runId.current = r.run_id;
      setStarted(true);
    } catch (e) {
      setError(String((e as Error).message));
    }
  };

  const busy = started && !finished;
  return (
    <div className="modal-backdrop">
      <div className="modal wide">
        <div className="row between">
          <h2>Fix config permissions of {root}</h2>
          <button disabled={busy} onClick={() => onClose(!!finished?.ok)}>Close</button>
        </div>
        {error && <div className="error" role="alert">{error}</div>}
        {plan && !started && (
          <>
            <p className="muted">
              Standard: configs owned by {plan.dev_user} (you edit them without sudo), group of {plan.run_as} (Odoo reads them),
              nobody else (they hold passwords). Folders used only by this installation get setgid, so new configs get the same group.
            </p>
            {plan.changes.length === 0 ? <p>Nothing to change.</p> : (
              <table><tbody>
                {plan.changes.map((c) => (
                  <tr key={c.path}><td>{c.path}</td><td className="muted">{c.current}</td><td>→ {c.target}</td></tr>
                ))}
              </tbody></table>
            )}
            {plan.notes.map((n) => <p key={n} className="muted">{n}</p>)}
            {plan.changes.length > 0 && (
              <>
                <h3>Root script (one sudo prompt)</h3>
                <pre className="script">{plan.script}</pre>
                <div className="row end"><button className="primary" onClick={apply}>Apply</button></div>
              </>
            )}
          </>
        )}
        {started && (
          <>
            <p className="muted">{finished ? (finished.ok ? "Done." : "Failed.") : "Running…"}</p>
            <pre className="script">{log}</pre>
            {finished?.ok && finished.receipt && <p className="muted">The old owners and modes are in {finished.receipt}.</p>}
            {finished && !finished.ok && <p className="muted">{finished.error}</p>}
          </>
        )}
      </div>
    </div>
  );
}
