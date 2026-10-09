import { Copy, FileCode2, ListChecks, Pencil, Play, Plus, RefreshCw, RotateCcw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { RUN_TONE, type RunRow, type WorkflowDoc, type WorkflowRow } from "../features/Tasks";
import { rpc } from "../rpc";
import { useApp } from "../state/app";
import { ago, Badge, Callout, EmptyState, Loading } from "../ui/primitives";
import { Disclosure } from "../ui/Job";
import { Panel, View, ViewHead } from "./common";

/** K1-K3: saved workflows and recipes, one workflow's parameters and text, and the run history with retry. */
export function TasksView({ name }: { name?: string }) {
  const app = useApp();
  const [rows, setRows] = useState<WorkflowRow[] | null>(null);
  const [doc, setDoc] = useState<WorkflowDoc | null>(null);
  const [runs, setRuns] = useState<RunRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const v = app.versions.tasks ?? 0;

  const load = useCallback(async () => {
    setError(null);
    try {
      setRows(await rpc.request<WorkflowRow[]>("tasks.list"));
      setRuns(await rpc.request<RunRow[]>("tasks.history", name ? { workflow: name } : {}));
    } catch (e) { setError(String((e as Error).message)); }
  }, [name]);
  useEffect(() => { load(); }, [load, v]);
  useEffect(() => rpc.on("tasks.finished", () => { load(); }), [load]);
  useEffect(() => {
    setDoc(null);
    if (name) rpc.request<WorkflowDoc>("tasks.read", { name }).then(setDoc).catch((e) => setError(String(e.message)));
  }, [name, v]);

  const current = rows?.find((r) => r.name === name) ?? null;
  const remove = async () => {
    if (!name || !(await app.confirm({ title: `Delete ${name}?`, body: "The file is moved aside to .trash-NAME-TIME.toml in the workflows folder, not deleted. Run history stays.", confirm: "Move aside", danger: true }))) return;
    try { await rpc.request("tasks.delete", { name }); app.nav({ view: "tasks" }); app.invalidate("tasks"); }
    catch (e) { app.onError(String((e as Error).message)); }
  };

  return (
    <View>
      <ViewHead icon={<ListChecks />} title="Tasks"
        subtitle="Workflows of typed operations: snapshot, upgrade, test, pull, clone, start. Every step is planned and shown before it runs; steps that change data wait for you; a failed step stops the run and can be retried."
        actions={<>
          <button className="btn ghost sm" onClick={load}><RefreshCw />Refresh</button>
          <button className="btn sm primary" onClick={() => app.setDialog({ kind: "task-edit" })}><Plus />New workflow</button>
        </>} />
      {error && <Callout tone="bad">{error}</Callout>}
      {!rows ? <Loading>Reading workflows…</Loading> : (
        <div className="tasks-layout">
          <Panel flush title="Workflows">
            <div className="list" role="listbox" aria-label="Workflows">
              {rows.map((r) => (
                <div key={r.name} role="option" aria-selected={r.name === name} tabIndex={0}
                  className={`list-row clickable${r.name === name ? " selected" : ""}`}
                  onClick={() => app.nav({ view: "tasks", name: r.name })}
                  onKeyDown={(e) => { if (e.key === "Enter") app.nav({ view: "tasks", name: r.name }); }}>
                  <div className="stack tight grow" style={{ minWidth: 0, gap: 2 }}>
                    <span className="row tight"><span className="strong truncate">{r.title ?? r.name}</span>{r.source === "built-in" && <Badge>recipe</Badge>}</span>
                    <span className="xs dim truncate">{r.error ? <span className="bad-text">{r.error}</span> : `${r.name} · ${r.steps} step(s)`}</span>
                  </div>
                </div>
              ))}
            </div>
          </Panel>
          <div className="stack">
            {!name ? (
              <EmptyState icon={<ListChecks />} title="Pick a workflow"
                actions={<button className="btn primary" onClick={() => app.nav({ view: "tasks", name: "safe-upgrade" })}>Open Safe upgrade</button>}>
                Recipes are read-only; copy one to change it. Saved workflows live in ~/.config/odoo-dev-panel/workflows.
              </EmptyState>
            ) : !current ? <Callout tone="warn">No workflow {name}.</Callout> : (
              <Panel title={<div className="row tight"><h2>{current.title ?? current.name}</h2>{current.source === "built-in" ? <Badge>recipe</Badge> : <Badge mono>{current.name}.toml</Badge>}</div>}
                actions={<div className="row tight">
                  {current.source === "saved" ? (
                    <>
                      <button className="btn ghost sm" onClick={remove}><Trash2 />Delete…</button>
                      <button className="btn sm" onClick={() => app.setDialog({ kind: "task-edit", name: current.name })}><Pencil />Edit</button>
                    </>
                  ) : <button className="btn sm" onClick={() => app.setDialog({ kind: "task-edit", copyOf: current.name })}><Copy />Copy to edit</button>}
                  <button className="btn sm primary" disabled={!!current.error} onClick={() => app.setDialog({ kind: "task-run", name: current.name })}><Play />Run…</button>
                </div>}>
                <div className="stack tight">
                  {current.error && <Callout tone="bad">{current.error}</Callout>}
                  {current.description && <p className="muted">{current.description}</p>}
                  {doc && (
                    <>
                      <div className="row tight wrap">
                        {Object.entries(doc.params).map(([k, s]) => <Badge key={k} mono title={s.description}>{k}: {s.kind}</Badge>)}
                        {doc.derived.map((k) => <Badge key={k} mono title="Derived from the config">{k}: derived</Badge>)}
                      </div>
                      <Disclosure title={<span className="row tight"><FileCode2 style={{ width: 13 }} />Workflow file{doc.path ? ` (${doc.path})` : ""}</span>}>
                        <pre className="block">{doc.text}</pre>
                      </Disclosure>
                    </>
                  )}
                </div>
              </Panel>
            )}
            <RunHistory runs={runs} showName={!name} />
          </div>
        </div>
      )}
    </View>
  );
}

function RunHistory({ runs, showName }: { runs: RunRow[] | null; showName: boolean }) {
  const app = useApp();
  return (
    <Panel flush title={<div className="row"><h2>Runs</h2><span className="xs dim">~/.local/state/odoo-dev-panel/tasks/history.jsonl</span></div>}>
      {!runs ? <Loading /> : runs.length === 0 ? <p className="muted" style={{ padding: 12 }}>No runs yet.</p> : (
        <div className="list">
          {runs.map((r) => {
            const retryable = r.status !== "ok" && r.status !== "running";
            return (
              <div key={r.run} className="list-row" style={{ alignItems: "flex-start" }}>
                <Badge tone={RUN_TONE[r.status] ?? "idle"}>{r.status}</Badge>
                <div className="stack tight grow" style={{ minWidth: 0, gap: 3 }}>
                  <span className="row tight wrap">
                    {showName && <span className="strong">{r.title}</span>}
                    <span className="xs dim" title={r.at}>{ago(r.at)}</span>
                    <span className="xs dim mono">run {r.run}</span>
                    {r.retry_of && <span className="xs dim">retry of {r.retry_of}</span>}
                  </span>
                  <span className="row tight wrap">
                    {Object.entries(r.params).map(([k, v]) => <Badge key={k} mono>{k}={Array.isArray(v) ? v.join(",") : v}</Badge>)}
                  </span>
                  <ol className="xs" style={{ margin: 0, paddingLeft: 18 }} start={r.start + 1}>
                    {r.steps.map((s) => (
                      <li key={s.index} className={s.status === "ok" ? "" : "bad-text"}>
                        {s.title} — {s.status}{s.summary ? `: ${s.summary}` : ""}{s.seconds != null ? ` (${s.seconds}s)` : ""}
                      </li>
                    ))}
                  </ol>
                </div>
                {retryable && <button className="btn sm" onClick={() => app.setDialog({ kind: "task-run", name: r.workflow, retryOf: r.run })}><RotateCcw />Retry…</button>}
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
}
