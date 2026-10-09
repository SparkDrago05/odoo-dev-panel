import { BookOpen, Download, FileCog, FolderGit2, Import, Layers, RefreshCw, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { rpc } from "../rpc";
import { useApp } from "../state/app";
import type { AddonsProposal, ProfileInfo, RepoPlan } from "../types";
import { Dialog } from "../ui/Dialog";
import { Checks, Disclosure, Steps, useJob } from "../ui/Job";
import { ActionMenu } from "../ui/Menu";
import { Badge, Callout, CheckBox, EmptyState, Field, Loading } from "../ui/primitives";
import { PlanTable, type RepoDone, useRepoProgress } from "./RepoDialogs";

type ProfileList = { profiles: ProfileInfo[]; folder: string; org: { path: string; exists: boolean; error: string | null } };

export function useProfiles() {
  const [data, setData] = useState<ProfileList | null>(null);
  const load = useCallback(() => { rpc.request<ProfileList>("profile.list").then(setData).catch(() => setData({ profiles: [], folder: "", org: { path: "", exists: false, error: null } })); }, []);
  useEffect(load, [load]);
  return { data, load };
}

/** Settings panel: saved profiles, the org overlay, import, view, move aside. */
export function ProfilesPanel() {
  const app = useApp();
  const { data, load } = useProfiles();
  const [view, setView] = useState<{ name: string; text: string } | null>(null);
  const [orgPath, setOrgPath] = useState("");
  useEffect(() => { if (data) setOrgPath(data.org.path); }, [data]);

  const importFile = async () => {
    const { path } = await rpc.request<{ path: string | null }>("desktop.pickFile", { title: "Import a profile (.toml)", kind: "any" }).catch((e) => { app.onError(String(e.message)); return { path: null }; });
    if (!path) return;
    if (await app.act("importing", () => rpc.request("profile.import", { path }), "Profile imported")) load();
  };
  const show = async (name: string) => {
    const out = await rpc.request<{ text: string }>("profile.read", { name }).catch((e) => { app.onError(String(e.message)); return null; });
    if (out) setView({ name, text: out.text });
  };
  const remove = async (p: ProfileInfo) => {
    if (!await app.confirm({ title: `Move profile ${p.name} aside?`, body: <>The file is renamed to <code>.trash-{p.name}-&lt;time&gt;.toml</code> in the same folder. Installations and repositories are not touched.</>, confirm: "Move aside" })) return;
    if (await app.act("moving the profile", () => rpc.request("profile.delete", { name: p.name }), `${p.name} moved aside`)) load();
  };
  const saveOrg = async (reset: boolean) => {
    if (await app.act("saving", () => rpc.request("profile.org", { path: reset ? null : orgPath.trim() }), "Org overlay location saved")) load();
  };

  return (
    <div className="stack">
      {!data ? <Loading /> : (
        <>
          <span className="muted small">Profiles hold install defaults and repositories (a bundle is a profile with only repositories). TOML files in <code>{data.folder}</code>; no passwords ever.</span>
          {data.profiles.length === 0 ? (
            <EmptyState icon={<BookOpen />} title="No profiles yet">Save one from the review step of New installation, export an installation, or import a file.</EmptyState>
          ) : (
            <div className="panel" style={{ overflow: "hidden" }}>
              <div className="list">
                {data.profiles.map((p) => (
                  <div key={p.name} className="list-row">
                    <BookOpen style={{ width: 14, height: 14, color: "var(--text-3)" }} />
                    <div className="grow" style={{ display: "grid", minWidth: 0 }}>
                      <span className="strong truncate">{p.title ?? p.name} <span className="dim mono xs">{p.name}</span></span>
                      <span className="meta xs truncate">{p.error ?? p.description ?? `${p.repos} repositories`}</span>
                    </div>
                    {p.error ? <Badge tone="bad">invalid</Badge> : <Badge>{p.odoo_version ? `Odoo ${p.odoo_version}` : "any version"}</Badge>}
                    <Badge mono>{p.repos} repo{p.repos === 1 ? "" : "s"}</Badge>
                    <div className="row-actions"><ActionMenu items={[
                      { label: "View TOML", icon: <FileCog />, onSelect: () => show(p.name) },
                      "sep",
                      { label: "Move aside", icon: <Trash2 />, onSelect: () => remove(p) },
                    ]} /></div>
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="row tight">
            <button className="btn sm" onClick={importFile}><Import />Import…</button>
            <button className="btn ghost sm" onClick={load}><RefreshCw />Refresh</button>
          </div>
          {view && <Disclosure title={`${view.name}.toml`} open><pre className="block">{view.text}</pre></Disclosure>}
          <Field label={<><Layers style={{ width: 12, height: 12 }} /> Org overlay</>} hint={data.org.error ? <span className="bad-text">{data.org.error}</span>
            : data.org.exists ? "Applies under every profile (org repositories, branches). The app never fetches it; distribute it yourselves." : "Not present. Optional."}>
            <div className="row tight">
              <input className="mono grow" value={orgPath} onChange={(e) => setOrgPath(e.target.value)} />
              <button className="btn sm" disabled={!orgPath.startsWith("/") || orgPath === data.org.path} onClick={() => saveOrg(false)}>Save</button>
              <button className="btn ghost sm" onClick={() => saveOrg(true)}>Default</button>
            </div>
          </Field>
        </>
      )}
    </div>
  );
}

/** Export an installation as a sanitized profile: shows the TOML and what was left out, then saves it. */
export function ExportProfileDialog({ root, onClose }: { root: string; onClose: () => void }) {
  const app = useApp();
  const [out, setOut] = useState<{ text: string; notes: string[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState(root.split("/").filter(Boolean).pop() ?? "export");
  useEffect(() => { rpc.request<{ text: string; notes: string[] }>("profile.export", { root }).then(setOut).catch((e) => setError(String(e.message))); }, [root]);
  const save = async () => {
    if (await app.act("saving the profile", () => rpc.request("profile.export", { root, name: name.trim() }), `Saved profile ${name.trim()}`)) onClose();
  };
  return (
    <Dialog size="lg" icon={<Upload />} title="Export as a profile" subtitle={root} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Close</button>
        <button className="btn ghost" disabled={!out} onClick={() => out && navigator.clipboard.writeText(out.text)}>Copy TOML</button>
        <button className="btn primary" disabled={!out || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,60}$/.test(name.trim())} onClick={save}><Download />Save profile</button></>}>
      {error && <Callout tone="bad">{error}</Callout>}
      {!out && !error && <Loading>Reading repositories and config…</Loading>}
      {out && (
        <>
          <Callout tone="info">Repositories (URL without user info, branch, folder), version, Python and safe tuning options. Never passwords, database settings, ports, data paths or database names.</Callout>
          <Field label="Profile name"><input value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <pre className="block tall">{out.text}</pre>
          {out.notes.length > 0 && <Disclosure title={`Left out or worth knowing (${out.notes.length})`} open>
            <ul className="xs dim" style={{ margin: 0, paddingLeft: 18 }}>{out.notes.map((n) => <li key={n}>{n}</li>)}</ul>
          </Disclosure>}
        </>
      )}
    </Dialog>
  );
}

/** Apply a profile's repositories to an existing installation: plan, clone, then a confirmed addons_path change. */
export function ApplyProfileDialog({ installation, onClose }: { installation: string; onClose: (changed: boolean) => void }) {
  const app = useApp();
  const { data } = useProfiles();
  const [profile, setProfile] = useState("");
  const [plan, setPlan] = useState<RepoPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [proposal, setProposal] = useState<AddonsProposal[] | null>(null);
  const [pick, setPick] = useState<Set<string>>(new Set());
  const [applied, setApplied] = useState<Record<string, string>>({});
  const job = useJob<RepoDone>("git");
  const progress = useRepoProgress();

  useEffect(() => {
    setPlan(null); setError(null);
    if (!profile) return;
    rpc.request<RepoPlan>("repo.plan", { op: "bundle", installation, profile }).then(setPlan).catch((e) => setError(String(e.message)));
  }, [profile, installation]);
  const f = job.finished;
  useEffect(() => {
    if (!f?.results) return;
    const repos = f.results.filter((r) => (r.status === "ok" || r.status === "kept") && r.addons_path_entry).map((r) => r.repo);
    if (!repos.length) { setProposal([]); return; }
    rpc.request<AddonsProposal[]>("repo.addons_plan", { installation, repos }).then((rows) => {
      setProposal(rows);
      setPick(new Set(rows.filter((r) => r.add.length && r.writable).map((r) => r.path)));
    }).catch((e) => setError(String(e.message)));
  }, [f]); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    setError(null);
    try {
      const out = await rpc.request<{ run_id: string }>("repo.run", { op: "bundle", installation, profile });
      progress.track(out.run_id);
      job.begin(out.run_id);
    } catch (e) {
      setError(String((e as Error).message));
    }
  };
  const apply = async () => {
    for (const row of proposal ?? []) {
      if (!pick.has(row.path) || applied[row.path]) continue;
      try {
        const out = await rpc.request<{ backup: string | null }>("repo.addons_apply", { path: row.path, add: row.add, sha: row.sha });
        setApplied((a) => ({ ...a, [row.path]: out.backup ?? "unchanged" }));
      } catch (e) {
        setApplied((a) => ({ ...a, [row.path]: `failed: ${(e as Error).message}` }));
      }
    }
    app.scan();
  };
  const toUpdate = (proposal ?? []).filter((r) => r.add.length);

  return (
    <Dialog size="lg" icon={<FolderGit2 />} title="Apply a profile" subtitle={installation} onClose={() => onClose(!!f)} locked={job.busy}
      footer={<>
        <button className="btn" disabled={job.busy} onClick={() => onClose(!!f)}>{job.started ? "Close" : "Cancel"}</button>
        {!job.started && plan && <button className="btn primary" disabled={!plan.ok} onClick={start}><FolderGit2 />Clone {plan.counts.run}, keep {plan.items.filter((i) => i.skip && i.level === "ok").length}</button>}
        {f && toUpdate.length > 0 && <button className="btn primary" disabled={!pick.size || toUpdate.every((r) => applied[r.path])} onClick={apply}><FileCog />Update {pick.size} config(s)</button>}
      </>}>
      {error && <Callout tone="bad">{error}</Callout>}
      {!job.started && (
        <>
          <Field label="Profile" hint="Its repositories go into this installation with their folders. Ones already there are kept and recorded, never fetched or switched.">
            <select autoFocus value={profile} onChange={(e) => setProfile(e.target.value)}>
              <option value="">Choose…</option>
              {data?.profiles.filter((p) => p.repos > 0).map((p) => <option key={p.name} value={p.name} disabled={!!p.error}>{p.title ?? p.name} · {p.repos} repo(s)</option>)}
            </select>
          </Field>
          {profile && !plan && !error && <Loading>Checking destinations…</Loading>}
          {plan && <><Checks checks={plan.checks.filter((c) => !plan.items.some((i) => i.repo === c.id))} /><PlanTable plan={plan} rows={{}} /></>}
          {plan && <Steps steps={plan.steps} />}
        </>
      )}
      {job.started && plan && <PlanTable plan={plan} rows={progress.rows} results={f?.results} />}
      {f && proposal && (
        toUpdate.length === 0 ? <Callout tone="ok">No config needs a new addons_path entry.</Callout> : (
          <div className="stack tight">
            <span className="section-title">addons_path changes (nothing is written until you confirm)</span>
            {toUpdate.map((r) => (
              <div key={r.path} className="panel" style={{ padding: 10 }}>
                <CheckBox checked={pick.has(r.path)} disabled={!r.writable || !!applied[r.path]}
                  onChange={(on) => setPick((s) => { const n = new Set(s); if (on) n.add(r.path); else n.delete(r.path); return n; })}>
                  <span className="mono xs">{r.path}</span>{!r.writable && <Badge tone="warn">not writable: Fix permissions first</Badge>}
                </CheckBox>
                {r.add.map((a) => <div key={a} className="mono xs" style={{ color: "var(--success)", paddingLeft: 22 }}>+ {a}</div>)}
                {applied[r.path] && <div className="xs dim" style={{ paddingLeft: 22 }}>{applied[r.path].startsWith("failed") ? <span className="bad-text">{applied[r.path]}</span> : <>updated; backup <code>{applied[r.path]}</code></>}</div>}
              </div>
            ))}
          </div>
        )
      )}
    </Dialog>
  );
}
