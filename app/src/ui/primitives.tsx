import { AlertTriangle, Check, CheckCircle2, Copy, Info, XCircle } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";

export type Tone = "ok" | "warn" | "bad" | "info" | "accent" | "idle";

export function Dot({ tone = "idle", live, title }: { tone?: Tone; live?: boolean; title?: string }) {
  return <span className={`dot ${tone === "idle" ? "hollow" : tone}${live ? " live" : ""}`} title={title} aria-hidden={title ? undefined : true} />;
}

export function Badge({ tone, mono, children, title }: { tone?: Tone; mono?: boolean; children: ReactNode; title?: string }) {
  return <span className={`badge${tone && tone !== "idle" ? ` ${tone}` : ""}${mono ? " mono" : ""}`} title={title}>{children}</span>;
}

export function Field({ label, hint, children, extra }: { label: ReactNode; hint?: ReactNode; children: ReactNode; extra?: ReactNode }) {
  return (
    <label className="field">
      <span className="label">{label}{extra}</span>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}

export function CheckBox({ checked, onChange, children, disabled, title }: {
  checked: boolean; onChange: (v: boolean) => void; children: ReactNode; disabled?: boolean; title?: string;
}) {
  return (
    <label className={`check${disabled ? " disabled" : ""}`} title={title}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span>{children}</span>
    </label>
  );
}

export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: { value: T; label: ReactNode; icon?: ReactNode }[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>{o.icon}{o.label}</button>
      ))}
    </div>
  );
}

export function EmptyState({ icon, title, children, actions }: { icon: ReactNode; title: string; children?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="empty">
      <div className="icon">{icon}</div>
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}

export function Loading({ children = "Loading…" }: { children?: ReactNode }) {
  return <div className="loading" role="status"><span className="spinner" />{children}</div>;
}

const CALLOUT_ICON = { bad: XCircle, warn: AlertTriangle, info: Info, ok: CheckCircle2 };
export function Callout({ tone = "info", title, children, action }: { tone?: "bad" | "warn" | "info" | "ok"; title?: ReactNode; children?: ReactNode; action?: ReactNode }) {
  const Icon = CALLOUT_ICON[tone];
  return (
    <div className={`callout ${tone}`} role={tone === "bad" ? "alert" : undefined}>
      <Icon />
      <div className="grow">
        {title && <span className="strong">{title}</span>}
        {children && <span>{children}</span>}
      </div>
      {action}
    </div>
  );
}

export function CopyButton({ text, label = "Copy", small = true }: { text: string; label?: string; small?: boolean }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className={`btn ghost icon${small ? " sm" : ""}`}
      title={done ? "Copied" : label}
      aria-label={label}
      onClick={(e) => {
        e.stopPropagation();
        navigator.clipboard.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1400); }).catch(() => undefined);
      }}
    >
      {done ? <Check /> : <Copy />}
    </button>
  );
}

/** A command line with a copy button. */
export function Cmd({ children }: { children: string }) {
  return (
    <div className="cmd">
      <code>{children}</code>
      <CopyButton text={children} label="Copy command" />
    </div>
  );
}

export function KV({ items }: { items: [ReactNode, ReactNode, ("mono" | undefined)?][] }) {
  return (
    <dl className="kv">
      {items.map(([k, v, kind], i) => (
        <div key={i} style={{ display: "contents" }}>
          <dt>{k}</dt>
          <dd className={kind === "mono" ? "mono" : undefined}>{v ?? <span className="dim">–</span>}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Stat({ label, icon, value, sub }: { label: string; icon?: ReactNode; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="stat">
      <span className="label">{icon}{label}</span>
      <span className="value">{value}</span>
      {sub && <span className="sub">{sub}</span>}
    </div>
  );
}

export const mb = (n: number) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${(n / 1e6).toFixed(n < 1e7 ? 1 : 0)} MB`);

export function ago(iso: string | null | undefined): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function duration(from: string, to?: string | null): string {
  const a = new Date(from).getTime();
  const b = to ? new Date(to).getTime() : Date.now();
  if (Number.isNaN(a) || Number.isNaN(b)) return "";
  const s = Math.max(0, Math.round((b - a) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

/** Arrow-key movement through a list of selectable rows. */
export function listKeys<T>(items: T[], current: T | null | undefined, same: (a: T, b: T) => boolean, select: (item: T) => void) {
  return (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    if ((e.target as HTMLElement).closest("input, textarea, select")) return;
    e.preventDefault();
    if (!items.length) return;
    const i = current ? items.findIndex((x) => same(x, current)) : -1;
    const next = e.key === "ArrowDown" ? Math.min(items.length - 1, i + 1) : Math.max(0, i - 1);
    select(items[next]);
    requestAnimationFrame(() => {
      const el = (e.currentTarget as HTMLElement).querySelector<HTMLElement>('[aria-selected="true"]');
      el?.scrollIntoView({ block: "nearest" });
      el?.focus({ preventScroll: true });
    });
  };
}

/** True while the window is narrower than `limit` pixels. */
export function useNarrow(limit = 900) {
  const [narrow, setNarrow] = useState(() => window.innerWidth < limit);
  useEffect(() => {
    const onResize = () => setNarrow(window.innerWidth < limit);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [limit]);
  return narrow;
}
