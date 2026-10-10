import { AlertTriangle, ChevronRight, HeartPulse, Info, PackagePlus, RefreshCw, ShieldCheck, Stethoscope, Wrench, XCircle } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useMemo, useState } from "react";
import { Inspector, InspectorSection } from "../shell/Chrome";
import { installLabel } from "../shell/Sidebar";
import { useApp } from "../state/app";
import type { Finding } from "../types";
import { ago, Badge, Callout, Cmd, EmptyState, listKeys, Loading, Segmented } from "../ui/primitives";
import { View, ViewHead } from "./common";

const ICON = { error: XCircle, warning: AlertTriangle, info: Info };
const ORDER = { error: 0, warning: 1, info: 2 };
export const findingKey = (f: Finding) => `${f.code}:${f.subject}`;

export function FindingList({ findings, selected, onOpen }: { findings: Finding[]; selected?: string; onOpen: (f: Finding) => void }) {
  return (
    <div className="list" role="listbox" aria-label="Findings" onKeyDown={listKeys(findings, findings.find((f) => findingKey(f) === selected), (a, b) => findingKey(a) === findingKey(b), onOpen)}>
      {findings.map((f) => {
        const Icon = ICON[f.severity];
        const sel = selected === findingKey(f);
        return (
          <div key={findingKey(f)} className={`finding ${f.severity}`} role="option" aria-selected={sel} tabIndex={sel ? 0 : -1} onClick={() => onOpen(f)}>
            <Icon aria-label={f.severity} />
            <div className="stack tight" style={{ gap: 2, minWidth: 0 }}>
              <span className="t">{f.title}</span>
              {f.detail && <span className="d">{f.detail}</span>}
              <span className="c">{f.check} · {f.code} · {f.subject}</span>
            </div>
            <div className="row nowrap tight">
              {f.repair && <Badge tone="accent"><Wrench />fix available</Badge>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

type Group = "installation" | "severity";

/** Diagnostics: summary, filters, findings grouped by installation or severity, explanation and guided repair. */
export function DoctorView({ finding: selectedKey }: { finding?: string }) {
  const app = useApp();
  const { doctor, snap } = app;
  const [severity, setSeverity] = useState<"all" | Finding["severity"]>("all");
  const [group, setGroup] = useState<Group>("installation");
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const report = doctor.report;
  const shown = useMemo(() => (report?.findings ?? []).filter((f) => severity === "all" || f.severity === severity)
    .sort((a, b) => ORDER[a.severity] - ORDER[b.severity]), [report, severity]);
  const groups = useMemo(() => {
    const m = new Map<string, Finding[]>();
    for (const f of shown) {
      const k = group === "severity" ? f.severity : f.installation ?? "";
      m.set(k, [...(m.get(k) ?? []), f]);
    }
    return [...m.entries()].sort((a, b) => (group === "severity" ? ORDER[a[0] as Finding["severity"]] - ORDER[b[0] as Finding["severity"]] : a[0] === "" ? 1 : b[0] === "" ? -1 : a[0].localeCompare(b[0])));
  }, [shown, group]);
  const selected = report?.findings.find((f) => findingKey(f) === selectedKey) ?? null;
  const open = (f: Finding) => { app.nav({ view: "doctor", finding: findingKey(f) }); app.inspect(); };
  const label = (k: string) => {
    if (group === "severity") return { error: "Errors", warning: "Warnings", info: "Notes" }[k as Finding["severity"]];
    if (!k) return "Machine and orphan configs";
    const i = snap?.installations.find((x) => x.root === k);
    return i ? `Odoo ${i.version} · ${installLabel(i)}` : k;
  };
  const counts = report?.counts ?? {};

  return (
    <>
      <View>
        <ViewHead icon={<Stethoscope />} title="Doctor"
          subtitle={report ? <>Read-only checks · last run {ago(doctor.at)} · nothing changes unless you press Repair</> : "Read-only checks of venvs, configs, addons paths, git, ports, units, permissions and filestores."}
          actions={<button className="btn primary" onClick={app.runDoctor} disabled={doctor.running}><RefreshCw className={doctor.running ? "spin" : undefined} />{doctor.running ? "Checking…" : report ? "Check again" : "Run checks"}</button>} />

        {!report ? (
          doctor.running ? <Loading>Checking every installation…</Loading> : (
            <EmptyState icon={<Stethoscope />} title="Run the Doctor" actions={<button className="btn primary" onClick={app.runDoctor}><Stethoscope />Run checks</button>}>
              It reads, it does not change anything. Fixes it can make (venv rebuild, config permissions) always show their plan first.
            </EmptyState>
          )
        ) : (
          <>
            <div className="row">
              <button className="chip" aria-pressed={severity === "all"} onClick={() => setSeverity("all")}>All <span className="count">{report.findings.length}</span></button>
              <button className="chip" aria-pressed={severity === "error"} onClick={() => setSeverity("error")}><XCircle style={{ width: 12, height: 12, color: "var(--danger)" }} />Errors <span className="count">{counts.error ?? 0}</span></button>
              <button className="chip" aria-pressed={severity === "warning"} onClick={() => setSeverity("warning")}><AlertTriangle style={{ width: 12, height: 12, color: "var(--warning)" }} />Warnings <span className="count">{counts.warning ?? 0}</span></button>
              <button className="chip" aria-pressed={severity === "info"} onClick={() => setSeverity("info")}><Info style={{ width: 12, height: 12, color: "var(--info)" }} />Notes <span className="count">{counts.info ?? report.findings.filter((f) => f.severity === "info").length}</span></button>
              <span className="grow" />
              <Segmented label="Group by" value={group} onChange={setGroup} options={[{ value: "installation", label: "By installation" }, { value: "severity", label: "By severity" }]} />
            </div>
            {report.findings.length === 0 ? (
              <EmptyState icon={<HeartPulse />} title="No problems found">Every check passed.</EmptyState>
            ) : shown.length === 0 ? (
              <p className="muted">No findings of this kind.</p>
            ) : (
              <div className="panel" style={{ overflow: "hidden" }}>
                {groups.map(([k, list]) => {
                  const isClosed = closed.has(k);
                  return (
                    <div key={k}>
                      <div className="list-group-head" role="button" tabIndex={0} aria-expanded={!isClosed}
                        onClick={() => setClosed((c) => { const n = new Set(c); if (n.has(k)) n.delete(k); else n.add(k); return n; })}
                        onKeyDown={(e) => { if (e.key === "Enter") (e.currentTarget as HTMLElement).click(); }}>
                        <ChevronRight style={{ transform: isClosed ? undefined : "rotate(90deg)", transition: "transform 0.18s" }} />
                        <span className="truncate">{label(k)}</span>
                        {group === "installation" && k && <span className="mono xs dim truncate">{k}</span>}
                        <span className="grow" />
                        {list.some((f) => f.severity === "error") && <span className="count-pill bad">{list.filter((f) => f.severity === "error").length}</span>}
                        {list.some((f) => f.severity === "warning") && <span className="count-pill warn">{list.filter((f) => f.severity === "warning").length}</span>}
                      </div>
                      <AnimatePresence initial={false}>
                        {!isClosed && (
                          <motion.div initial={{ height: 0 }} animate={{ height: "auto" }} exit={{ height: 0 }} transition={{ duration: 0.18 }} style={{ overflow: "hidden" }}>
                            <FindingList findings={list} selected={selectedKey} onOpen={open} />
                          </motion.div>
                        )}
                      </AnimatePresence>
                    </div>
                  );
                })}
              </div>
            )}
            {report.not_checked.length > 0 && <Callout tone="info" title="Not checked">{report.not_checked.join("; ")}</Callout>}
          </>
        )}
      </View>
      {selected && (
        <Inspector title={{ error: "Error", warning: "Warning", info: "Note" }[selected.severity]}>
          <InspectorSection title="Finding">
            <strong style={{ overflowWrap: "anywhere" }}>{selected.title}</strong>
            {selected.detail && <span className="muted">{selected.detail}</span>}
            <code className="xs dim break">{selected.subject}</code>
          </InspectorSection>
          <InspectorSection title="Why it matters"><p>{selected.why}</p></InspectorSection>
          {selected.install && selected.install.length > 0 && selected.installation && (
            <InspectorSection title="Install">
              <button className="btn primary" onClick={() => app.setDialog({ kind: "python-action", root: selected.installation!, params: { op: "install", packages: selected.install } })}>
                <PackagePlus />Install {selected.install.length} package(s)…
              </button>
              <span className="xs dim">Into the existing venv with uv, as its run-as user. Shows the plan first; nothing is removed. Run the checks again afterwards.</span>
            </InspectorSection>
          )}
          {selected.repair === "venv" && selected.installation && (
            <InspectorSection title="Fix">
              <button className="btn primary" onClick={() => app.setDialog({ kind: "repair-venv", root: selected.installation! })}><Wrench />Repair venv…</button>
              <button className="btn" onClick={() => app.nav({ view: "installation", root: selected.installation!, tab: "python" })}>Open the Python tab</button>
              <span className="xs dim">Shows the plan first. The old venv stays until the new one validates. The checks run again afterwards.</span>
            </InspectorSection>
          )}
          {selected.repair === "config-perms" && selected.installation && (
            <InspectorSection title="Fix">
              <button className="btn primary" onClick={() => app.setDialog({ kind: "fix-perms", root: selected.installation! })}><ShieldCheck />Fix permissions…</button>
              <span className="xs dim">Shows the root script first; one sudo prompt. The checks run again afterwards.</span>
            </InspectorSection>
          )}
          {selected.commands.length > 0 && (
            <InspectorSection title="Suggested commands (not run by the app)">
              {selected.commands.map((c) => <Cmd key={c}>{c}</Cmd>)}
            </InspectorSection>
          )}
          {selected.installation && (
            <InspectorSection title="Affects">
              <a onClick={() => app.nav({ view: "installation", root: selected.installation! })} className="mono small">{selected.installation}</a>
            </InspectorSection>
          )}
          <span className="xs dim mono">{selected.check} · {selected.code}</span>
        </Inspector>
      )}
    </>
  );
}
