import { Eye, EyeOff, FileCog, FilePlus2, Lock, Save, ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { rpc } from "../rpc";
import { useApp } from "../state/app";
import { Dialog } from "../ui/Dialog";
import { type Finished, JobView, useJob } from "../ui/Job";
import { Badge, Callout, Loading, Segmented } from "../ui/primitives";
import { type Changes, ConfigForm, type FormData } from "./ConfigForm";

type Issue = { level: "error" | "warning"; key: string | null; text: string };
type Access = { owner: string; group: string; mode: string; writable: boolean; others_read: boolean };
type Opened = { path: string; text: string; sha: string; access: Access; issues: Issue[] };
type Form = FormData & { text: string; issues: Issue[] };
type Change = { path: string; kind: string; current: string; target: string };
type PermsPlan = { root: string; run_as: string; dev_user: string; changes: Change[]; kept: string[]; notes: string[]; script: string };

const MASK = "********";

/** Config editor: form and raw views of the same text, validation while typing, save with a backup, refused when
 * the file changed since it was opened. Passwords stay masked unless revealed. */
export function ConfigEditorDialog({ path, root, onClose }: { path: string; root: string | null; onClose: (changed: boolean) => void }) {
  const app = useApp();
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
  useEffect(() => { load(current, false); }, [current]); // eslint-disable-line react-hooks/exhaustive-deps

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
  }, [text, opened]); // eslint-disable-line react-hooks/exhaustive-deps

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

  const discardOk = async () => !dirty || app.confirm({ title: "Discard unsaved changes?", body: "Your edits to this config are not saved.", confirm: "Discard", danger: true });
  const toggleReveal = async () => { if (await discardOk()) load(current, !revealed); };
  const close = async () => { if (!saving && (await discardOk())) onClose(changed); };

  return (
    <>
      <Dialog size="xl" icon={<FileCog />} title={current.split("/").pop()} subtitle={current} onClose={close} locked={saving}
        footer={opened && (
          <>
            <div className="row grow">
              <input value={copyName} placeholder="new config name" aria-label="New config name" style={{ width: 180 }} onChange={(e) => setCopyName(e.target.value)} />
              <button className="btn" disabled={!copyName || dirty} title={dirty ? "Save or discard your changes first" : "Copy this config to a new file"} onClick={copy}>
                <FilePlus2 />Copy to new config
              </button>
            </div>
            {dirty && <Badge tone="warn">Unsaved changes</Badge>}
            <button className="btn" onClick={close}>Close</button>
            <button className="btn primary" disabled={readOnly || !dirty || errors.length > 0 || saving} onClick={save}>
              <Save />{errors.length > 0 ? "Fix the errors first" : saving ? "Saving…" : "Save"}
            </button>
          </>
        )}>
        {error && <Callout tone="bad">{error}</Callout>}
        {notice && <Callout tone="ok">{notice}</Callout>}
        {!opened && !error && <Loading>Opening config…</Loading>}
        {opened && (
          <>
            <div className="row">
              <Segmented label="Editor view" value={view} onChange={setView} options={[{ value: "form", label: "Form" }, { value: "raw", label: "Raw" }]} />
              <Badge mono>{opened.access.owner}:{opened.access.group} {opened.access.mode}</Badge>
              {readOnly && <Badge tone="warn"><Lock />read-only for you</Badge>}
              {opened.access.others_read && <Badge tone="bad">every local user can read it</Badge>}
              <span className="grow" />
              <button className="btn ghost sm" onClick={toggleReveal} title={`Passwords show as ${MASK}; leave them to keep the current value, or type a new one.`}>
                {revealed ? <EyeOff /> : <Eye />}{revealed ? "Hide passwords" : "Reveal passwords"}
              </button>
            </div>
            {readOnly && root && (
              <Callout tone="warn" action={<button className="btn sm" onClick={() => setFixing(true)}><ShieldCheck />Fix permissions</button>}>
                You cannot save this file. Fix permissions makes you the owner and the run-as user's group the reader (one sudo prompt).
              </Callout>
            )}
            {issues.length > 0 && (
              <div className="checks">
                {issues.map((i, n) => (
                  <div key={n} className={`check-row ${i.level === "error" ? "fail" : "warn"}`} style={{ gridTemplateColumns: "10px minmax(0,max-content) 1fr" }}>
                    <span className={`dot ${i.level === "error" ? "bad" : "warn"}`} />
                    <span className="id">{i.key ?? ""}</span>
                    <span className="detail">{i.text}</span>
                  </div>
                ))}
              </div>
            )}
            {view === "form" ? (
              form ? <ConfigForm data={form} readOnly={readOnly} revealed={revealed} apply={apply} /> : <Loading>Reading options…</Loading>
            ) : (
              <textarea className="config" value={text} readOnly={readOnly} spellCheck={false} aria-label="Config text" onChange={(e) => setText(e.target.value)} />
            )}
            <span className="xs dim">Passwords show as {MASK}; leave them to keep the current value, or type a new one. Saving keeps a 0600 backup of the previous version.</span>
          </>
        )}
      </Dialog>
      {fixing && root && (
        <FixPermissionsDialog root={root} onClose={(applied) => { setFixing(false); if (applied) { setChanged(true); load(current, revealed); } }} />
      )}
    </>
  );
}

/** Config permission standard for one installation: review the root script, apply with one sudo prompt. */
export function FixPermissionsDialog({ root, onClose }: { root: string; onClose: (applied: boolean) => void }) {
  const [plan, setPlan] = useState<PermsPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const job = useJob<Finished & { changed?: number; receipt?: string | null }>("repair");

  useEffect(() => {
    rpc.request<PermsPlan>("perms.plan", { root }).then(setPlan).catch((e) => setError(String(e.message)));
  }, [root]);

  const apply = async () => {
    setError(null);
    try {
      job.begin((await rpc.request<{ run_id: string }>("perms.run", { root })).run_id);
    } catch (e) {
      setError(String((e as Error).message));
    }
  };

  return (
    <Dialog size="lg" icon={<ShieldCheck />} title="Fix config permissions" subtitle={root} onClose={() => onClose(!!job.finished?.ok)} locked={job.busy}
      footer={<>
        <button className="btn" disabled={job.busy} onClick={() => onClose(!!job.finished?.ok)}>Close</button>
        {!job.started && plan && plan.changes.length > 0 && <button className="btn primary" onClick={apply}>Apply (one sudo prompt)</button>}
      </>}>
      {error && <Callout tone="bad">{error}</Callout>}
      {!plan && !error && <Loading>Reading current owners and modes…</Loading>}
      {plan && !job.started && (
        <>
          <p className="muted">
            Standard: configs owned by <b>{plan.dev_user}</b> (you edit them without sudo), group of <b>{plan.run_as}</b> (Odoo reads them),
            nobody else (they hold passwords). Folders used only by this installation get setgid, so new configs get the same group.
          </p>
          {plan.changes.length === 0 ? <Callout tone="ok">Nothing to change.</Callout> : (
            <div className="panel">
              <table>
                <thead><tr><th>Path</th><th>Now</th><th>After</th></tr></thead>
                <tbody>
                  {plan.changes.map((c) => (
                    <tr key={c.path}><td className="mono small break">{c.path}</td><td className="mono small muted">{c.current}</td><td className="mono small">{c.target}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {plan.notes.map((n) => <p key={n} className="muted">{n}</p>)}
          {plan.changes.length > 0 && (
            <div className="stack tight">
              <span className="section-title">Root script (one sudo prompt)</span>
              <pre className="block">{plan.script}</pre>
            </div>
          )}
        </>
      )}
      {job.started && (
        <JobView job={job}>
          {job.finished?.ok && job.finished.receipt && <p className="muted">The old owners and modes are in <code>{job.finished.receipt}</code>.</p>}
        </JobView>
      )}
    </Dialog>
  );
}
