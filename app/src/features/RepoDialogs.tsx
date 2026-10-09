import {
  CheckCircle2, CircleSlash, Download, FileDiff, FolderGit2, GitBranch, GitCommitHorizontal, GitPullRequestArrow, Plus,
  RotateCcw, Square, XCircle,
} from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { rpc } from "../rpc";
import { useApp } from "../state/app";
import type { RepoDiff, RepoOp, RepoPlan, RepoResult, StepEvent } from "../types";
import { Dialog } from "../ui/Dialog";
import { Checks, Disclosure, type Finished, Steps, useJob } from "../ui/Job";
import { LogConsole } from "../ui/LogConsole";
import { Badge, Callout, CheckBox, Cmd, EmptyState, Field, Loading, Segmented } from "../ui/primitives";

const OP_META: Record<RepoOp, { title: string; icon: ReactNode; verb: string }> = {
  fetch: { title: "Fetch", icon: <Download />, verb: "Fetch" },
  pull: { title: "Pull (fast-forward only)", icon: <GitPullRequestArrow />, verb: "Pull" },
  switch: { title: "Switch branch", icon: <GitBranch />, verb: "Switch" },
  checkout: { title: "Check out a tag or commit", icon: <GitCommitHorizontal />, verb: "Check out" },
  clone: { title: "Clone", icon: <FolderGit2 />, verb: "Clone" },
};
const base = (p: string) => p.split("/").filter(Boolean).pop() ?? p;
type Done = Finished & { op: RepoOp; results?: RepoResult[]; counts?: Record<string, number> };
type RowState = "running" | "ok" | "fail";

function useDebounced<T>(value: T, ms = 350) {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}

/** Per-repository progress of a running repository job, from git.step events. */
function useRepoProgress() {
  const runId = useRef<string | null>(null);
  const [rows, setRows] = useState<Record<string, RowState>>({});
  useEffect(() => rpc.on("git.step", (e: StepEvent) => {
    if (e.run_id !== runId.current || e.status === "output") return;
    setRows((r) => ({ ...r, [e.step]: e.status === "start" ? "running" : e.status === "ok" ? "ok" : "fail" }));
  }), []);
  return { rows, track: (id: string) => { runId.current = id; setRows({}); } };
}

const STATUS_BADGE: Record<RepoResult["status"], ReactNode> = {
  ok: <Badge tone="ok"><CheckCircle2 />done</Badge>,
  failed: <Badge tone="bad"><XCircle />failed</Badge>,
  skipped: <Badge><CircleSlash />skipped</Badge>,
  cancelled: <Badge tone="warn"><Square />cancelled</Badge>,
};

/** Plan, per-repository table, then results with partial failures spelled out. */
function PlanTable({ plan, rows, results }: { plan: RepoPlan; rows: Record<string, RowState>; results?: RepoResult[] }) {
  const byRepo = new Map(results?.map((r) => [r.repo, r]));
  return (
    <div className="panel" style={{ overflow: "hidden" }}>
      <div className="list">
        {plan.items.map((i) => {
          const res = byRepo.get(i.repo);
          const live = rows[i.repo];
          return (
            <div key={i.repo} className="list-row" style={{ alignItems: "flex-start", paddingTop: 8, paddingBottom: 8 }}>
              <div className="grow stack tight" style={{ minWidth: 0 }}>
                <div className="row tight">
                  <span className="strong">{base(i.repo)}</span>
                  <span className="meta mono xs truncate" title={i.repo}>{i.repo}</span>
                </div>
                {i.skip ? <span className={`xs ${i.level === "fail" ? "bad-text" : "dim"}`}>Skipped: {i.skip}</span>
                  : i.commands.map((c) => <Cmd key={c}>{c}</Cmd>)}
                {res?.problem && <Callout tone="bad" title={res.problem.title}>{res.problem.detail}{res.problem.commands.map((c) => <Cmd key={c}>{c}</Cmd>)}</Callout>}
                {res?.status === "ok" && res.changed_files !== undefined && (
                  <span className="xs dim">
                    {res.changed_files} file(s) changed.
                    {res.changed_modules?.length ? <> Modules touched: <span className="mono">{res.changed_modules.join(", ")}</span>. Review them before upgrading; a changed file does not always need an upgrade.</> : ""}
                  </span>
                )}
              </div>
              <div style={{ flex: "none" }}>
                {res ? STATUS_BADGE[res.status] : live === "running" ? <Badge tone="info"><span className="spinner" style={{ width: 10, height: 10 }} />running</Badge>
                  : live ? STATUS_BADGE[live === "ok" ? "ok" : "failed"] : i.skip ? STATUS_BADGE.skipped : <Badge>queued</Badge>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function RepoOpDialog({ op, repos: initial, bulk, installation, onClose }: {
  op: Exclude<RepoOp, "clone">; repos?: string[]; bulk?: boolean; installation?: string; onClose: (changed: boolean) => void;
}) {
  const [repos, setRepos] = useState(initial ?? []);
  const [bulkMode, setBulkMode] = useState(!!bulk);
  const [ref, setRef] = useState("");
  const [confirm, setConfirm] = useState("");
  const [fetchBranch, setFetchBranch] = useState(false);
  const [plan, setPlan] = useState<RepoPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const job = useJob<Done>("git");
  const progress = useRepoProgress();
  const runId = useRef<string | null>(null);
  const needsRef = op === "switch" || op === "checkout";
  const dRef = useDebounced(ref.trim());
  const dConfirm = useDebounced(confirm.trim());

  const params = useMemo(() => {
    if (op === "switch") return { op, repo: repos[0], branch: dRef, fetch_branch: fetchBranch };
    if (op === "checkout") return { op, repo: repos[0], ref: dRef, confirm: dConfirm };
    return bulkMode ? { op, bulk: true, installation } : { op, repos };
  }, [op, repos, dRef, dConfirm, fetchBranch, bulkMode, installation]);

  useEffect(() => {
    if (job.started) return;
    if (needsRef && !dRef) { setPlan(null); return; }
    setError(null);
    rpc.request<RepoPlan>("repo.plan", params).then(setPlan).catch((e) => { setPlan(null); setError(String(e.message)); });
  }, [params]); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    setError(null);
    try {
      const out = await rpc.request<{ run_id: string }>("repo.run", params);
      runId.current = out.run_id;
      progress.track(out.run_id);
      job.begin(out.run_id);
    } catch (e) {
      setError(String((e as Error).message));
    }
  };
  const cancel = () => runId.current && rpc.request("repo.cancel", { run_id: runId.current }).catch(() => undefined);
  const f = job.finished;
  const failed = f?.results?.filter((r) => r.status === "failed" || r.status === "cancelled").map((r) => r.repo) ?? [];
  // Retry: plan again for the failed and cancelled repositories only.
  const retry = () => { setPlan(null); setBulkMode(false); setRepos(failed); job.reset(); progress.track(""); };

  const meta = OP_META[op];
  const counts = f?.counts;
  const subtitle = bulkMode ? `bulk selection${installation ? ` of ${installation}` : ""}` : repos.length === 1 ? repos[0] : `${repos.length} repositories`;
  return (
    <Dialog size="lg" icon={meta.icon} title={meta.title} subtitle={subtitle} onClose={() => onClose(!!f)} locked={job.busy}
      footer={<>
        {job.busy && <button className="btn" onClick={cancel} title="Stops after the repository that is running now"><Square />Stop after current</button>}
        {f && failed.length > 0 && op !== "checkout" && op !== "switch" && <button className="btn" onClick={retry}><RotateCcw />Retry {failed.length} failed</button>}
        <button className="btn" disabled={job.busy} onClick={() => onClose(!!f)}>{job.started ? "Close" : "Cancel"}</button>
        {!job.started && plan && <button className="btn primary" disabled={!plan.ok} onClick={start}>{meta.icon}{plan.ok ? `${meta.verb} ${plan.counts.run} repositor${plan.counts.run === 1 ? "y" : "ies"}` : "Nothing can run"}</button>}
      </>}>
      {error && <Callout tone="bad">{error}</Callout>}
      {op === "pull" && !job.started && <Callout tone="info">Fast-forward only. Repositories with uncommitted changes, a detached HEAD, no upstream or a diverged branch are skipped with the reason. Nothing is merged, rebased or reset.</Callout>}
      {op === "switch" && !job.started && (
        <>
          <Field label="Branch" hint="Uncommitted changes block the switch; nothing is discarded.">
            <input autoFocus className="mono" value={ref} onChange={(e) => setRef(e.target.value)} placeholder="staging-19" spellCheck={false} />
          </Field>
          <CheckBox checked={fetchBranch} onChange={setFetchBranch}>Fetch this branch first (needed in a single-branch or shallow clone)</CheckBox>
        </>
      )}
      {op === "checkout" && !job.started && (
        <>
          <Callout tone="warn">A detached checkout leaves the branch. Commits made there are easy to lose; switch back to a branch afterwards.</Callout>
          <Field label="Tag or commit"><input autoFocus className="mono" value={ref} onChange={(e) => setRef(e.target.value)} placeholder="v1.4.0 or a1b2c3d" spellCheck={false} /></Field>
          <Field label="Type it again to confirm"><input className="mono" value={confirm} onChange={(e) => setConfirm(e.target.value)} spellCheck={false} /></Field>
        </>
      )}
      {!plan && !error && (!needsRef || dRef) && <Loading>Inspecting repositories…</Loading>}
      {plan && (
        <>
          {!job.started && <Checks checks={plan.checks.filter((c) => !plan.items.some((i) => i.repo === c.id))} />}
          {counts && (
            <Callout tone={counts.failed ? "bad" : counts.cancelled ? "warn" : "ok"}
              title={[counts.ok && `${counts.ok} done`, counts.failed && `${counts.failed} failed`, counts.skipped && `${counts.skipped} skipped`, counts.cancelled && `${counts.cancelled} cancelled`].filter(Boolean).join(" · ") || "Nothing ran"}>
              {counts.failed ? "Each failure is explained below. The other repositories were not affected." : null}
            </Callout>
          )}
          <PlanTable plan={plan} rows={progress.rows} results={f?.results} />
          {f && !f.ok && f.error && <Callout tone="bad">{f.error}</Callout>}
          {job.started && <Disclosure title="Git output"><div className="job-log"><LogConsole text={job.log} jobs empty="Waiting for Git…" /></div></Disclosure>}
        </>
      )}
    </Dialog>
  );
}

const PURPOSES = ["custom", "community", "enterprise", "themes", "other"];

/** Add a repository to an installation: clone into a nested folder, or register an existing one. */
export function RepoAddDialog({ installation, onClose }: { installation: string; onClose: (changed: boolean) => void }) {
  const app = useApp();
  const [mode, setMode] = useState<"clone" | "existing">("clone");
  const [url, setUrl] = useState("");
  const [dest, setDest] = useState("");
  const [destTouched, setDestTouched] = useState(false);
  const [ref, setRef] = useState("");
  const [full, setFull] = useState(false);
  const [purpose, setPurpose] = useState("custom");
  const [group, setGroup] = useState("");
  const [addons, setAddons] = useState(true);
  const [bulk, setBulk] = useState(true);
  const [path, setPath] = useState("");
  const [plan, setPlan] = useState<RepoPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const job = useJob<Done>("git");
  const progress = useRepoProgress();

  useEffect(() => {
    if (destTouched) return;
    const name = url.trim().replace(/\/+$/, "").split(/[/:]/).pop()?.replace(/\.git$/, "") ?? "";
    setDest(name ? `custom/${name}` : "");
  }, [url, destTouched]);

  const fields = useMemo(() => ({ purpose, addons, bulk, ...(group.trim() ? { group: group.trim() } : {}) }), [purpose, addons, bulk, group]);
  const params = useMemo(() => ({ op: "clone", installation, url: url.trim(), destination: dest.trim(), ref: ref.trim() || null, shallow: !full, fields }),
    [installation, url, dest, ref, full, fields]);
  const dParams = useDebounced(params, 400);

  useEffect(() => {
    if (mode !== "clone" || job.started || !dParams.url || !dParams.destination) { if (!job.started) setPlan(null); return; }
    setError(null);
    rpc.request<RepoPlan>("repo.plan", dParams).then(setPlan).catch((e) => { setPlan(null); setError(String(e.message)); });
  }, [dParams, mode]); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    setError(null);
    try {
      const out = await rpc.request<{ run_id: string }>("repo.run", params);
      progress.track(out.run_id);
      job.begin(out.run_id);
    } catch (e) {
      setError(String((e as Error).message));
    }
  };
  const register = async () => {
    const ok = await app.act("registering", () => rpc.request("repo.register", { path: path.trim(), installation, fields }), "Repository added");
    if (ok) onClose(true);
  };
  const f = job.finished;
  const entry = f?.results?.[0]?.addons_path_entry;
  const instance = app.snap?.instances.find((i) => i.installation === installation);
  const segments = dest.trim().split("/").filter(Boolean);

  return (
    <Dialog size="lg" icon={<Plus />} title="Add repository" subtitle={installation} onClose={() => onClose(!!f?.ok)} locked={job.busy}
      footer={<>
        <button className="btn" disabled={job.busy} onClick={() => onClose(!!f?.ok)}>{job.started ? "Close" : "Cancel"}</button>
        {mode === "clone" && !job.started && <button className="btn primary" disabled={!plan?.ok} onClick={start}><FolderGit2 />Clone</button>}
        {mode === "existing" && <button className="btn primary" disabled={!path.trim().startsWith("/")} onClick={register}><Plus />Add</button>}
      </>}>
      {error && <Callout tone="bad">{error}</Callout>}
      {!job.started && (
        <>
          <Segmented label="Source" value={mode} onChange={setMode}
            options={[{ value: "clone", label: "Clone from a URL", icon: <Download /> }, { value: "existing", label: "Existing folder", icon: <FolderGit2 /> }]} />
          {mode === "clone" ? (
            <>
              <Field label="Git URL" hint="SSH (git@host:org/repo.git) or HTTPS. Your SSH agent or Git credential helper authenticates; never put a password or token in the URL.">
                <input autoFocus className="mono" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="git@example.com:education/admissions.git" spellCheck={false} />
              </Field>
              <Field label="Destination" hint="Relative to the installation root. Nested folders are kept as typed.">
                <input className="mono" value={dest} onChange={(e) => { setDest(e.target.value); setDestTouched(true); }} placeholder="custom/education/admissions" spellCheck={false} />
              </Field>
              {segments.length > 0 && (
                <div className="dest-tree mono xs" aria-label="Destination tree">
                  <div className="dim">{installation}</div>
                  {segments.map((s, n) => <div key={n} style={{ paddingLeft: 14 * (n + 1) }}>{n === segments.length - 1 ? <strong>└─ {s}/</strong> : <>└─ {s}/</>}</div>)}
                </div>
              )}
              <Disclosure title="Options">
                <div className="stack">
                  <Field label="Branch, tag or commit" hint="Empty: the remote's default branch. A commit id means a full clone, then a detached checkout.">
                    <input className="mono" value={ref} onChange={(e) => setRef(e.target.value)} placeholder="staging-19" spellCheck={false} />
                  </Field>
                  <CheckBox checked={full} onChange={setFull}>Full history (default: shallow, single branch)</CheckBox>
                  <RepoFields purpose={purpose} setPurpose={setPurpose} group={group} setGroup={setGroup} addons={addons} setAddons={setAddons} bulk={bulk} setBulk={setBulk} />
                </div>
              </Disclosure>
              {plan && <><Checks checks={plan.checks} /><Steps steps={plan.steps} /></>}
            </>
          ) : (
            <>
              <Field label="Repository folder" hint="An existing Git work tree. Nothing is changed in it; it is only added to the list.">
                <input autoFocus className="mono" value={path} onChange={(e) => setPath(e.target.value)} placeholder="/opt/odoo19/custom/hr/payroll" spellCheck={false} />
              </Field>
              <RepoFields purpose={purpose} setPurpose={setPurpose} group={group} setGroup={setGroup} addons={addons} setAddons={setAddons} bulk={bulk} setBulk={setBulk} />
            </>
          )}
        </>
      )}
      {job.started && plan && (
        <>
          <PlanTable plan={plan} rows={progress.rows} results={f?.results} />
          {f?.ok && f.results?.[0]?.status === "ok" && (
            <Callout tone="ok" title="Cloned and added to the repository list"
              action={addons && entry && instance ? <button className="btn sm" onClick={() => app.setDialog({ kind: "config", path: instance.path, root: installation })}>Edit config…</button> : undefined}>
              {addons && entry ? <>To load its modules, add <code>{entry}</code> to the addons_path of the instances that need it. The config is not changed automatically.</> : null}
              {addons && !entry ? "No module folder found at its top level; it was not suggested for the addons_path." : null}
            </Callout>
          )}
          <Disclosure title="Git output"><div className="job-log"><LogConsole text={job.log} jobs empty="Waiting for Git…" /></div></Disclosure>
        </>
      )}
    </Dialog>
  );
}

function RepoFields({ purpose, setPurpose, group, setGroup, addons, setAddons, bulk, setBulk }: {
  purpose: string; setPurpose: (v: string) => void; group: string; setGroup: (v: string) => void;
  addons: boolean; setAddons: (v: boolean) => void; bulk: boolean; setBulk: (v: boolean) => void;
}) {
  return (
    <div className="stack tight">
      <div className="grid2">
        <Field label="Purpose"><select value={purpose} onChange={(e) => setPurpose(e.target.value)}>{PURPOSES.map((p) => <option key={p}>{p}</option>)}</select></Field>
        <Field label="Group" hint="Optional label, e.g. hr or cms"><input value={group} onChange={(e) => setGroup(e.target.value)} /></Field>
      </div>
      <CheckBox checked={addons} onChange={setAddons}>Contributes modules to the addons_path</CheckBox>
      <CheckBox checked={bulk} onChange={setBulk}>Include in bulk fetch and pull</CheckBox>
    </div>
  );
}

/** Changed files with their diff, and commits between HEAD and upstream. Read-only. */
export function RepoDiffDialog({ path, onClose }: { path: string; onClose: () => void }) {
  const [data, setData] = useState<RepoDiff | null>(null);
  const [file, setFile] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { rpc.request<RepoDiff>("repo.diff", { path }).then(setData).catch((e) => setError(String(e.message))); }, [path]);
  useEffect(() => {
    if (!file) { setText(null); return; }
    rpc.request<RepoDiff>("repo.diff", { path, file }).then((d) => setText(d.diff ?? "")).catch((e) => setError(String(e.message)));
  }, [path, file]);
  return (
    <Dialog size="xl" icon={<FileDiff />} title={`Changes in ${base(path)}`} subtitle={path} onClose={onClose}
      footer={<button className="btn" onClick={onClose}>Close</button>}>
      {error && <Callout tone="bad">{error}</Callout>}
      {!data && !error && <Loading />}
      {data && (
        <div className="diff-layout">
          <div className="stack tight">
            <span className="section-title">Working tree ({data.files.length}{data.truncated ? "+" : ""})</span>
            {data.files.length === 0 ? <span className="muted small">No local changes.</span> : (
              <div className="list panel" role="listbox" aria-label="Changed files">
                {data.files.map((f) => (
                  <div key={f.path} className="list-row clickable" role="option" aria-selected={file === f.path} onClick={() => setFile(f.path)} style={{ minHeight: 30 }}>
                    <span className="mono xs" style={{ width: 22, color: "var(--text-3)" }}>{f.index || " "}{f.worktree || " "}</span>
                    <span className="mono xs truncate">{f.path}</span>
                  </div>
                ))}
              </div>
            )}
            <CommitList title="Incoming (on upstream)" items={data.incoming} />
            <CommitList title="Outgoing (not on upstream)" items={data.outgoing} />
          </div>
          <div className="diff-view">
            {!file ? <EmptyState icon={<FileDiff />} title="Pick a file">Its diff against HEAD shows here.</EmptyState>
              : text === null ? <Loading /> : text === "" ? <p className="muted">No diff to show (untracked or binary file).</p>
                : <pre className="diff mono xs">{text.split("\n").map((l, n) => <span key={n} className={l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : l.startsWith("@@") ? "hunk" : undefined}>{l}{"\n"}</span>)}</pre>}
          </div>
        </div>
      )}
    </Dialog>
  );
}

function CommitList({ title, items }: { title: string; items: RepoDiff["incoming"] }) {
  if (!items.length) return null;
  return (
    <div className="stack tight" style={{ marginTop: 8 }}>
      <span className="section-title">{title} · {items.length}</span>
      {items.map((c) => <div key={c.sha} className="row tight xs"><span className="mono dim">{c.sha}</span><span className="truncate" title={`${c.author} · ${c.date}`}>{c.subject}</span></div>)}
    </div>
  );
}
