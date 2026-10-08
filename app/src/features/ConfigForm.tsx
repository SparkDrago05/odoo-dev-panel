import { ArrowDown, ArrowUp, Plus, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Dot, Field } from "../ui/primitives";

export type AddonsEntry = { path: string; state: "ok" | "missing" | "other"; installation: string | null; version: string | null };
export type FormData = { options: Record<string, string> | null; addons: AddonsEntry[] };
export type Changes = Record<string, string | null>;

type Kind = "text" | "number" | "bool" | "choice" | "secret";
type FieldDef = { key: string; label: string; kind?: Kind; choices?: string[]; hint?: string };

const LOG_LEVELS = ["info", "debug", "debug_rpc", "debug_rpc_answer", "debug_sql", "test", "warn", "error", "critical", "runbot", "notset"];

const GROUPS: [string, FieldDef[]][] = [
  ["Server", [
    { key: "http_port", label: "HTTP port", kind: "number" },
    { key: "http_interface", label: "HTTP interface", hint: "empty: all interfaces" },
    { key: "gevent_port", label: "Gevent port", kind: "number", hint: "longpolling_port before Odoo 16" },
    { key: "workers", label: "Workers", kind: "number", hint: "0: one threaded process" },
    { key: "max_cron_threads", label: "Cron threads", kind: "number" },
    { key: "proxy_mode", label: "Proxy mode", kind: "bool" },
  ]],
  ["Database", [
    { key: "db_host", label: "Host", hint: "empty: Unix socket" },
    { key: "db_port", label: "Port", kind: "number" },
    { key: "db_user", label: "User", hint: "empty: the OS user" },
    { key: "db_password", label: "Password", kind: "secret" },
    { key: "db_name", label: "Database", hint: "empty: choose in the browser" },
    { key: "dbfilter", label: "Database filter", hint: "regular expression, e.g. ^%d$" },
    { key: "list_db", label: "List databases", kind: "bool" },
  ]],
  ["Files and logs", [
    { key: "data_dir", label: "Data folder", hint: "filestore and sessions" },
    { key: "logfile", label: "Log file", hint: "empty: output only" },
    { key: "log_level", label: "Log level", kind: "choice", choices: LOG_LEVELS },
  ]],
  ["Limits (with workers)", [
    { key: "limit_memory_soft", label: "Memory soft (bytes)", kind: "number" },
    { key: "limit_memory_hard", label: "Memory hard (bytes)", kind: "number" },
    { key: "limit_time_cpu", label: "CPU time (s)", kind: "number" },
    { key: "limit_time_real", label: "Real time (s)", kind: "number" },
    { key: "limit_request", label: "Requests per worker", kind: "number" },
  ]],
  ["Security", [
    { key: "admin_passwd", label: "Master password", kind: "secret", hint: "database manager" },
  ]],
];
const FORM_KEYS = new Set(["addons_path", ...GROUPS.flatMap(([, fields]) => fields.map((f) => f.key))]);

export function ConfigForm({ data, readOnly, revealed, apply }: {
  data: FormData; readOnly: boolean; revealed: boolean; apply: (changes: Changes) => void;
}) {
  if (data.options === null) return <p className="muted">The text is not a valid config. Fix it in the Raw view first.</p>;
  const options = data.options;
  const other = Object.keys(options).filter((k) => !FORM_KEYS.has(k)).sort();
  return (
    <div className="stack loose">
      <section className="config-group">
        <span className="section-title">addons_path</span>
        <AddonsPath entries={data.addons} readOnly={readOnly} save={(paths) => apply({ addons_path: paths.length ? paths.join(",") : null })} />
      </section>
      {GROUPS.map(([title, fields]) => (
        <section key={title} className="config-group">
          <span className="section-title">{title}</span>
          <div className="grid3">
            {fields.map((f) => (
              <Input key={f.key} field={f} value={options[f.key]} readOnly={readOnly} revealed={revealed} save={(v) => apply({ [f.key]: v })} />
            ))}
          </div>
        </section>
      ))}
      {other.length > 0 && <p className="muted small">Other options in this file (edit them in Raw): <span className="mono">{other.join(", ")}</span></p>}
      <p className="xs dim">A field applies when you press Enter or leave it. An empty field removes the option, so Odoo uses its default.</p>
    </div>
  );
}

function Input({ field, value, readOnly, revealed, save }: {
  field: FieldDef; value: string | undefined; readOnly: boolean; revealed: boolean; save: (v: string | null) => void;
}) {
  const kind = field.kind ?? "text";
  // Odoo writes "False" for an unset text option; show it as empty.
  const shown = value === undefined || (value === "False" && kind !== "bool") ? "" : value;
  const [draft, setDraft] = useState(shown);
  useEffect(() => setDraft(shown), [shown]);
  const commit = (v: string) => { if (v.trim() !== shown) save(v.trim() === "" ? null : v.trim()); };
  const label = <span className="truncate" title={field.key}>{field.label}</span>;
  const hint = <><code>{field.key}</code>{field.hint && ` · ${field.hint}`}</>;

  if (kind === "bool" || kind === "choice") {
    const choices = kind === "bool" ? ["True", "False"] : field.choices ?? [];
    return (
      <Field label={label} hint={hint}>
        <select value={shown} disabled={readOnly} onChange={(e) => save(e.target.value === "" ? null : e.target.value)}>
          <option value="">(default)</option>
          {shown && !choices.includes(shown) && <option value={shown}>{shown}</option>}
          {choices.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </Field>
    );
  }
  return (
    <Field label={label} hint={hint}>
      <input value={draft} readOnly={readOnly} placeholder="default" spellCheck={false} autoComplete="off"
        className={kind === "number" || kind === "secret" ? "mono" : undefined}
        type={kind === "secret" && !revealed ? "password" : "text"} inputMode={kind === "number" ? "numeric" : undefined}
        onChange={(e) => setDraft(e.target.value)} onBlur={(e) => commit(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") commit(draft); if (e.key === "Escape" && draft !== shown) { e.stopPropagation(); setDraft(shown); } }} />
    </Field>
  );
}

const NOTE: Record<AddonsEntry["state"], string> = { ok: "", missing: "does not exist: Odoo refuses to start", other: "another installation" };

function describe(e: AddonsEntry): string {
  const parts = [NOTE[e.state]];
  if (e.version) parts.push(`Odoo ${e.version}`);
  else if (e.state === "ok" && !e.installation) parts.push("outside any installation");
  if (e.state === "other" && e.installation) parts.push(e.installation);
  return parts.filter(Boolean).join(" · ");
}

function AddonsPath({ entries, readOnly, save }: { entries: AddonsEntry[]; readOnly: boolean; save: (paths: string[]) => void }) {
  const [adding, setAdding] = useState("");
  const paths = entries.map((e) => e.path);
  const move = (from: number, to: number) => {
    const next = [...paths];
    next.splice(to, 0, ...next.splice(from, 1));
    save(next);
  };
  const add = () => {
    const p = adding.trim().replace(/\/+$/, "");
    if (!p) return;
    if (paths.includes(p)) return setAdding("");
    save([...paths, p]);
    setAdding("");
  };
  return (
    <div className="stack tight">
      <div className="panel">
        {entries.length === 0 && <p className="muted" style={{ padding: 10 }}>Empty: Odoo uses only its own addons.</p>}
        {entries.map((e, n) => (
          <div key={`${n}:${e.path}`} className="addons-row">
            <Dot tone={e.state === "missing" ? "bad" : e.state === "other" ? "warn" : "ok"} />
            <div className="grow" style={{ display: "grid" }}>
              <code>{e.path}</code>
              {describe(e) && <span className={`xs ${e.state === "missing" ? "" : "dim"}`} style={e.state === "missing" ? { color: "var(--danger)" } : undefined}>{describe(e)}</span>}
            </div>
            <div className="row nowrap tight">
              <button className="btn ghost icon sm" disabled={readOnly || n === 0} aria-label="Move up" title="Move up" onClick={() => move(n, n - 1)}><ArrowUp /></button>
              <button className="btn ghost icon sm" disabled={readOnly || n === entries.length - 1} aria-label="Move down" title="Move down" onClick={() => move(n, n + 1)}><ArrowDown /></button>
              <button className="btn ghost icon sm" disabled={readOnly} aria-label="Remove" title="Remove" onClick={() => save(paths.filter((_, i) => i !== n))}><X /></button>
            </div>
          </div>
        ))}
      </div>
      {!readOnly && (
        <div className="row nowrap">
          <input className="mono grow" value={adding} placeholder="/path/to/addons" spellCheck={false} aria-label="Add addons folder"
            onChange={(e) => setAdding(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") add(); }} />
          <button className="btn" disabled={!adding.trim()} onClick={add}><Plus />Add</button>
        </div>
      )}
    </div>
  );
}
