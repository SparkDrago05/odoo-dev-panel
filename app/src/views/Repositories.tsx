import { Download, FolderGit2, GitPullRequestArrow, Layers, Plus, RefreshCw } from "lucide-react";
import { type ReactNode, useMemo, useState } from "react";
import { BulkBar, matches, RepoDetails, RepoFilters, RepoList, useRepos } from "../features/Repos";
import { Inspector } from "../shell/Chrome";
import { useApp } from "../state/app";
import { Badge, EmptyState, Loading } from "../ui/primitives";
import { View, ViewHead } from "./common";

/** Repository list with filters, multi-select, and its inspector (rendered by the caller next to the view, like every
 * other inspector). Used by the Repositories section (all installations) and an installation's Repositories tab. */
export function useRepoWorkspace({ root, selected, onSelect, enabled = true }: {
  root?: string; selected?: string; onSelect: (path: string) => void; enabled?: boolean;
}): { body: ReactNode; inspector: ReactNode } {
  const app = useApp();
  const { repos, load, loading } = useRepos(root, enabled);
  const [filter, setFilter] = useState("all");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const shown = useMemo(() => (repos ?? []).filter((r) => matches(r, filter)), [repos, filter]);
  const current = repos?.find((r) => r.path === selected) ?? null;

  const body = (
    <>
      <div className="row wrap" style={{ justifyContent: "space-between", gap: 8 }}>
        {repos && <RepoFilters value={filter} onChange={setFilter} repos={repos} />}
        <div className="row tight">
          <button className="btn ghost sm" onClick={load} disabled={loading}><RefreshCw />{loading ? "Reading…" : "Refresh"}</button>
          <button className="btn sm" disabled={!repos?.length} title="Every repository selected for bulk operations"
            onClick={() => app.setDialog({ kind: "repo-op", op: "fetch", bulk: true, installation: root })}><Download />Fetch all</button>
          <button className="btn sm" disabled={!repos?.length} onClick={() => app.setDialog({ kind: "repo-op", op: "pull", bulk: true, installation: root })}><GitPullRequestArrow />Pull all</button>
          {root && <button className="btn sm" onClick={() => app.setDialog({ kind: "profile-apply", installation: root })}><Layers />Apply profile…</button>}
          {root && <button className="btn sm primary" onClick={() => app.setDialog({ kind: "repo-add", installation: root })}><Plus />Add repository</button>}
        </div>
      </div>
      <BulkBar picked={picked} clear={() => setPicked(new Set())} />
      {!repos ? <Loading>Reading repositories…</Loading> : repos.length === 0 ? (
        <EmptyState icon={<FolderGit2 />} title="No Git repositories"
          actions={root ? <button className="btn primary" onClick={() => app.setDialog({ kind: "repo-add", installation: root })}><Plus />Add repository</button> : undefined}>
          {root ? "No Git work tree was found under this installation or its addons paths." : "No installation has a Git work tree under its root or addons paths."}
        </EmptyState>
      ) : shown.length === 0 ? <p className="muted">No repository matches this filter.</p> : (
        <RepoList repos={shown} root={root} selected={selected} onSelect={(r) => { onSelect(r.path); app.inspect(); }}
          picked={picked} setPicked={setPicked} reload={load} groupBy={root ? "folder" : "installation"} />
      )}
    </>
  );
  const inspector = current && (
    <Inspector title={<span className="row tight"><FolderGit2 style={{ width: 14, height: 14 }} />{current.name}<Badge>{current.purpose}</Badge></span>}>
      <RepoDetails r={current} reload={load} />
    </Inspector>
  );
  return { body, inspector };
}

export function RepositoriesView({ path }: { path?: string }) {
  const app = useApp();
  const ws = useRepoWorkspace({ selected: path, onSelect: (p) => app.nav({ view: "repos", path: p }) });
  return (
    <>
      <View>
        <ViewHead icon={<FolderGit2 />} title="Repositories"
          subtitle="Git work trees of every installation: Community, Enterprise, themes and custom addons. Read-only until you pick an action; nothing destructive is ever run." />
        {ws.body}
      </View>
      {ws.inspector}
    </>
  );
}
