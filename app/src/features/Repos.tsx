import {
  ArrowDown, ArrowUp, Code2, Copy, Download, FileDiff, FolderOpen, GitBranch, GitCommitHorizontal, GitPullRequestArrow,
  RefreshCw, TerminalSquare, Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { rpc } from "../rpc";
import { InspectorSection } from "../shell/Chrome";
import { useApp } from "../state/app";
import type { Repo, RepoProblem } from "../types";
import { ActionMenu, ContextMenu, type MenuEntry } from "../ui/Menu";
import { ago, Badge, Callout, CheckBox, Cmd, Dot, KV, type Tone } from "../ui/primitives";

/** Repositories from the core (`repo.list`), reloaded after every repository job. */
export function useRepos(installation?: string, enabled = true) {
  const app = useApp();
  const [repos, setRepos] = useState<Repo[] | null>(null);
  const [loading, setLoading] = useState(false);
  const load = useCallback(async () => {
    if (!enabled) return;
    setLoading(true);
    try {
      const out = await rpc.request<{ repos: Repo[] }>("repo.list", installation ? { installation } : {});
      setRepos(out.repos);
    } catch (e) {
      app.onError(String((e as Error).message));
      setRepos((r) => r ?? []);
    } finally {
      setLoading(false);
    }
  }, [installation, enabled, app.onError]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [load, app.versions.repos]);
  useEffect(() => rpc.on("git.finished", () => { load(); }), [load]);
  return { repos, load, loading };
}

export function repoTone(r: Repo): Tone {
  const s = r.state;
  if (!s) return r.missing ? "bad" : "idle";
  if (!s.ok || s.conflicted || s.problems.some((p) => p.level === "error")) return "bad";
  if (s.dirty || s.problems.some((p) => p.level === "warn" && !p.heuristic)) return "warn";
  if (s.behind) return "info";
  return "ok";
}

const PURPOSE_TONE: Record<string, Tone | undefined> = { community: "accent", enterprise: "accent", themes: "info" };

export function relativeOf(r: Repo, root?: string) {
  const link = root ? r.installations.find((i) => i.root === root) : r.installations[0];
  return link?.relative ?? r.path;
}

/** Folder that holds the repository, relative to the installation: custom/cms for custom/cms/admissions. */
function groupOf(r: Repo, root?: string) {
  const rel = relativeOf(r, root);
  if (rel.startsWith("/")) return "Outside the installation";
  const parts = rel.split("/");
  return parts.length > 1 ? parts.slice(0, -1).join("/") : "Top level";
}

export function SyncBadges({ r }: { r: Repo }) {
  const s = r.state;
  if (!s) return r.missing ? <Badge tone="bad">missing</Badge> : null;
  if (!s.ok) return <Badge tone="bad">unreadable</Badge>;
  return (
    <>
      <Badge mono tone={s.detached ? "warn" : undefined} title={s.upstream ? `tracks ${s.upstream}` : "no upstream"}>
        {s.detached ? <GitCommitHorizontal /> : <GitBranch />}{s.branch ?? s.head?.slice(0, 10) ?? "empty"}
      </Badge>
      {(s.ahead ?? 0) > 0 && <Badge mono tone={s.behind ? "warn" : "info"} title="commits not on upstream"><ArrowUp />{s.ahead}</Badge>}
      {(s.behind ?? 0) > 0 && <Badge mono tone="info" title="upstream commits (as of the last fetch)"><ArrowDown />{s.behind}</Badge>}
      {s.dirty && <Badge tone="warn" title={`${s.staged} staged, ${s.modified} modified`}>{s.staged + s.modified} changed</Badge>}
      {s.conflicted > 0 && <Badge tone="bad">{s.conflicted} conflicts</Badge>}
      {s.untracked > 0 && <Badge title="untracked files">{s.untracked} new</Badge>}
      {s.shallow && <Badge title="shallow clone">shallow</Badge>}
      {s.worktree && <Badge title="linked worktree">worktree</Badge>}
      {s.foreign && <Badge title={`owned by ${s.owner}; read-only here`}>{s.owner}</Badge>}
    </>
  );
}

/** Contextual actions of one repository. Write operations open a dialog that shows the plan first. */
export function repoActions(r: Repo, app: ReturnType<typeof useApp>, reload: () => void): MenuEntry[] {
  const s = r.state;
  const writable = !!s && s.ok && !s.foreign;
  const open = (target: "ide" | "terminal" | "files") =>
    app.act(`opening ${r.name}`, () => rpc.request("repo.open", { path: r.path, target }));
  return [
    { section: "Git" },
    { label: "Fetch", icon: <Download />, disabled: !writable, onSelect: () => app.setDialog({ kind: "repo-op", op: "fetch", repos: [r.path] }) },
    { label: "Pull (fast-forward only)", icon: <GitPullRequestArrow />, disabled: !writable || !!s?.detached, onSelect: () => app.setDialog({ kind: "repo-op", op: "pull", repos: [r.path] }) },
    { label: "Switch branch…", icon: <GitBranch />, disabled: !writable, onSelect: () => app.setDialog({ kind: "repo-op", op: "switch", repos: [r.path] }) },
    { label: "Check out tag or commit…", icon: <GitCommitHorizontal />, disabled: !writable, onSelect: () => app.setDialog({ kind: "repo-op", op: "checkout", repos: [r.path] }) },
    { label: "Changes and commits", icon: <FileDiff />, disabled: !s?.ok, onSelect: () => app.setDialog({ kind: "repo-diff", path: r.path }) },
    "sep",
    { label: "Open in IDE", icon: <Code2 />, onSelect: () => open("ide") },
    { label: "Open terminal here", icon: <TerminalSquare />, onSelect: () => open("terminal") },
    { label: "Open folder", icon: <FolderOpen />, onSelect: () => open("files") },
    { label: "Copy path", icon: <Copy />, onSelect: () => navigator.clipboard.writeText(r.path) },
    { label: "Copy git status command", icon: <Copy />, onSelect: () => navigator.clipboard.writeText(`git -C '${r.path}' status`) },
    r.registered && "sep",
    r.registered && {
      label: "Forget (keep files)", icon: <Trash2 />, onSelect: async () => {
        if (!await app.confirm({ title: `Forget ${r.name}?`, body: <>Removes it and its associations from the repository list. The folder <code>{r.path}</code> is not touched.</>, confirm: "Forget" })) return;
        await app.act("forgetting", () => rpc.request("repo.forget", { path: r.path }), `${r.name} forgotten`);
        reload();
      },
    },
  ];
}

/** Repositories grouped by their folder, with multi-select for bulk operations. */
export function RepoList({ repos, root, selected, onSelect, picked, setPicked, reload, groupBy = "folder" }: {
  repos: Repo[]; root?: string; selected?: string; onSelect: (r: Repo) => void;
  picked: Set<string>; setPicked: (s: Set<string>) => void; reload: () => void; groupBy?: "folder" | "installation";
}) {
  const app = useApp();
  const groups = useMemo(() => {
    const out = new Map<string, Repo[]>();
    for (const r of repos) {
      const key = groupBy === "folder" ? groupOf(r, root) : (r.installations[0]?.root ?? "Not linked to an installation");
      out.set(key, [...(out.get(key) ?? []), r]);
    }
    return [...out.entries()];
  }, [repos, root, groupBy]);
  const toggle = (path: string, on: boolean) => {
    const next = new Set(picked);
    if (on) next.add(path); else next.delete(path);
    setPicked(next);
  };
  const toggleGroup = (items: Repo[], on: boolean) => {
    const next = new Set(picked);
    for (const r of items) if (on) next.add(r.path); else next.delete(r.path);
    setPicked(next);
  };
  return (
    <div className="panel" style={{ overflow: "hidden" }}>
      <div className="list" role="listbox" aria-label="Repositories" aria-multiselectable>
        {groups.map(([group, items]) => (
          <div key={group}>
            <div className="list-group-head" style={{ cursor: "default" }}>
              <input type="checkbox" aria-label={`Select all in ${group}`} checked={items.every((r) => picked.has(r.path))}
                onChange={(e) => toggleGroup(items, e.target.checked)} />
              <FolderOpen />
              <span className="mono truncate">{group}</span>
              <span className="count-pill">{items.length}</span>
            </div>
            {items.map((r) => (
              <ContextMenu key={r.path} items={repoActions(r, app, reload)}>
                <div className="list-row clickable" role="option" aria-selected={selected === r.path} tabIndex={selected === r.path ? 0 : -1}
                  onClick={() => onSelect(r)} onKeyDown={(e) => { if (e.key === "Enter") onSelect(r); }} style={{ minHeight: 50 }}>
                  <input type="checkbox" aria-label={`Select ${r.name}`} checked={picked.has(r.path)} onClick={(e) => e.stopPropagation()}
                    onChange={(e) => toggle(r.path, e.target.checked)} />
                  <Dot tone={repoTone(r)} />
                  <div className="grow" style={{ display: "grid", minWidth: 0 }}>
                    <span className="strong truncate">{r.name}</span>
                    <span className="meta mono xs truncate" title={r.path}>{groupBy === "folder" ? relativeOf(r, root) : r.path}</span>
                  </div>
                  <div className="row tight wrap repo-badges">
                    <Badge tone={PURPOSE_TONE[r.purpose]}>{r.purpose}</Badge>
                    <SyncBadges r={r} />
                  </div>
                  <div className="row-actions"><ActionMenu items={repoActions(r, app, reload)} /></div>
                </div>
              </ContextMenu>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/** Selection bar: bulk fetch and pull with a per-repository plan. */
export function BulkBar({ picked, clear }: { picked: Set<string>; clear: () => void }) {
  const app = useApp();
  if (!picked.size) return null;
  const repos = [...picked];
  return (
    <div className="bulk-bar" role="toolbar" aria-label="Selected repositories">
      <span className="strong">{picked.size} selected</span>
      <button className="btn sm" onClick={() => app.setDialog({ kind: "repo-op", op: "fetch", repos })}><Download />Fetch</button>
      <button className="btn sm" onClick={() => app.setDialog({ kind: "repo-op", op: "pull", repos })}><GitPullRequestArrow />Pull</button>
      <button className="btn ghost sm" onClick={clear}>Clear</button>
    </div>
  );
}

export function ProblemList({ problems }: { problems: RepoProblem[] }) {
  if (!problems.length) return <span className="muted small">No problems.</span>;
  return (
    <div className="stack tight">
      {problems.map((p) => (
        <Callout key={p.code + p.title} tone={p.level === "error" ? "bad" : p.level === "warn" ? "warn" : "info"}
          title={<>{p.title}{p.heuristic && <Badge>guess</Badge>}</>}>
          {p.detail}
          {p.commands.map((c) => <Cmd key={c}>{c}</Cmd>)}
        </Callout>
      ))}
    </div>
  );
}

/** Inspector sections of one repository. */
export function RepoDetails({ r, reload }: { r: Repo; reload: () => void }) {
  const app = useApp();
  const s = r.state;
  const [busy, setBusy] = useState(false);
  const refresh = async () => { setBusy(true); try { await reload(); } finally { setBusy(false); } };
  return (
    <>
      <InspectorSection title="State">
        {s ? (
          <KV items={[
            ["Branch", s.detached ? <span className="row tight"><GitCommitHorizontal />detached</span> : s.branch, "mono"],
            ["Commit", s.head?.slice(0, 12), "mono"],
            ["Last commit", s.subject ? <span title={s.committed ?? ""}>{s.subject} <span className="dim">· {ago(s.committed)}</span></span> : null],
            ["Upstream", s.upstream ?? <span className="dim">none</span>, "mono"],
            ["Ahead / behind", s.upstream ? `${s.ahead ?? "?"} / ${s.behind ?? "?"}` : null, "mono"],
            ["Changes", s.ok ? `${s.staged} staged · ${s.modified} modified · ${s.untracked} untracked` : null],
            ["Owner", s.owner, "mono"],
            ["Clone", [s.shallow && "shallow", s.worktree && "worktree"].filter(Boolean).join(", ") || "full"],
          ]} />
        ) : <span className="muted small">{r.missing ? "The folder is gone." : "Not inspected."}</span>}
      </InspectorSection>
      {s && Object.keys(s.remotes).length > 0 && (
        <InspectorSection title="Remotes">
          <KV items={Object.entries(s.remotes).map(([n, u]) => [n, <code className="xs break">{u}</code>])} />
        </InspectorSection>
      )}
      <InspectorSection title={`Installations (${r.installations.length})`}>
        {r.installations.length ? r.installations.map((l) => (
          <div key={l.root} className="stack tight">
            <a className="mono xs" onClick={() => app.nav({ view: "installation", root: l.root, tab: "repos" })}>{l.root}</a>
            <span className="xs dim mono">{l.relative ?? "outside the root"} · found via {l.sources.join(", ")}</span>
            {l.assoc && <span className="xs dim">{[l.assoc.purpose, l.assoc.group && `group ${l.assoc.group}`, l.assoc.preferred_branch && `prefers ${l.assoc.preferred_branch}`, l.assoc.bulk === false && "not in bulk"].filter(Boolean).join(" · ")}</span>}
          </div>
        )) : <span className="muted small">Registered, not linked to an installation.</span>}
      </InspectorSection>
      <InspectorSection title="Problems">
        <ProblemList problems={s?.problems ?? []} />
      </InspectorSection>
      <div className="action-stack">
        <button className="btn" disabled={!s?.ok || s.foreign} onClick={() => app.setDialog({ kind: "repo-op", op: "fetch", repos: [r.path] })}><Download />Fetch</button>
        <button className="btn" disabled={!s?.ok || s.foreign || s.detached} onClick={() => app.setDialog({ kind: "repo-op", op: "pull", repos: [r.path] })}><GitPullRequestArrow />Pull</button>
        <button className="btn" disabled={!s?.ok} onClick={() => app.setDialog({ kind: "repo-diff", path: r.path })}><FileDiff />Changes</button>
        <button className="btn ghost" disabled={busy} onClick={refresh}><RefreshCw />Refresh</button>
      </div>
    </>
  );
}

export function RepoFilters({ value, onChange, repos }: { value: string; onChange: (v: string) => void; repos: Repo[] }) {
  const count = (f: string) => repos.filter((r) => matches(r, f)).length;
  const opts: [string, string][] = [["all", "All"], ["changed", "Changed"], ["behind", "Behind"], ["problems", "Problems"], ["branch", "Branch mismatch"]];
  return (
    <div className="row tight wrap" role="group" aria-label="Filter">
      {opts.map(([v, label]) => (
        <button key={v} className="chip" aria-pressed={value === v} onClick={() => onChange(v)}>{label}<span className="count">{count(v)}</span></button>
      ))}
    </div>
  );
}

export function matches(r: Repo, f: string) {
  const s = r.state;
  switch (f) {
    case "changed": return !!s && (s.dirty || s.untracked > 0);
    case "behind": return !!s && (s.behind ?? 0) > 0;
    case "problems": return !s || s.problems.some((p) => p.level !== "info");
    case "branch": return !!s && s.problems.some((p) => p.code === "branch-mismatch");
    default: return true;
  }
}

export function SelectAll({ repos, picked, setPicked }: { repos: Repo[]; picked: Set<string>; setPicked: (s: Set<string>) => void }) {
  const all = repos.length > 0 && repos.every((r) => picked.has(r.path));
  return <CheckBox checked={all} onChange={(on) => setPicked(on ? new Set(repos.map((r) => r.path)) : new Set())}>Select all</CheckBox>;
}
