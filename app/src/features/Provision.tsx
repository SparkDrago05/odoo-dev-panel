import { ArrowLeft, ArrowRight, BookmarkPlus, ClipboardPaste, FolderGit2, FolderOpen, FolderTree, Lock, PackagePlus, Plus, Stethoscope, Trash2 } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { rpc } from "../rpc";
import { useApp } from "../state/app";
import type { Check, ProfileInfo, ProfileResolved, ProvisionPlan, Step } from "../types";
import { Dialog } from "../ui/Dialog";
import { Checks, Disclosure, type Finished, JobView, Steps, useJob } from "../ui/Job";
import { Badge, Callout, CheckBox, Field, Loading, Segmented } from "../ui/primitives";

type Done = Finished & { root: string; run_as: string; conf_path: string };
const VERSIONS = [20, 19, 18, 17, 16, 15];
type Stage = "setup" | "sources" | "review" | "run";
const STAGES: { id: Stage; label: string }[] = [
  { id: "setup", label: "Setup" }, { id: "sources", label: "Sources" }, { id: "review", label: "Review" }, { id: "run", label: "Install" },
];

export type RepoRow = {
  key: number; name: string; url: string; branch: string; destination: string; destTouched: boolean;
  addons: boolean; purpose: string; group: string; shallow: boolean; origin?: string;
};
type Install = {
  run_as: string; root: string; python: string; config_name: string; http_port: string; odoo_git: string; odoo_branch: string;
  enterprise_git: string; enterprise_branch: string; enterprise_archive: string;
};
const INSTALL_EMPTY: Install = {
  run_as: "", root: "", python: "", config_name: "", http_port: "", odoo_git: "", odoo_branch: "",
  enterprise_git: "", enterprise_branch: "", enterprise_archive: "",
};

let rowKey = 0;
const repoName = (url: string) => url.trim().replace(/\/+$/, "").split(/[/:]/).pop()?.replace(/\.git$/, "") ?? "";
export function newRow(r: Partial<RepoRow> = {}): RepoRow {
  const name = r.name || repoName(r.url ?? "");
  return { key: ++rowKey, name, url: "", branch: "", destination: name ? `custom/${name}` : "", destTouched: !!r.destination,
    addons: true, purpose: "custom", group: "", shallow: true, ...r };
}
export const rowToRepo = (r: RepoRow) => {
  const out: Record<string, unknown> = { url: r.url.trim() };
  if (r.name.trim()) out.name = r.name.trim();
  if (r.branch.trim()) out.branch = r.branch.trim();
  if (r.destination.trim()) out.destination = r.destination.trim().replace(/^\/+|\/+$/g, "");
  if (!r.addons) out.addons = false;
  if (r.purpose !== "custom") out.purpose = r.purpose;
  if (r.group.trim()) out.group = r.group.trim();
  if (!r.shallow) out.shallow = false;
  return out;
};

/** Repository rows: URL, branch, nested destination, addons role. Destinations follow the name until edited. */
export function RepoRows({ rows, onChange }: { rows: RepoRow[]; onChange: (rows: RepoRow[]) => void }) {
  const [paste, setPaste] = useState<string | null>(null);
  const update = (key: number, patch: Partial<RepoRow>) => onChange(rows.map((r) => {
    if (r.key !== key) return r;
    const next = { ...r, ...patch };
    if (patch.url !== undefined && !r.name) next.name = "";
    const name = next.name || repoName(next.url);
    if (!next.destTouched) next.destination = name ? `custom/${name}` : "";
    return next;
  }));
  const addPasted = () => {
    // Lines in the old format: [NAME=]URL[#BRANCH]
    const added = (paste ?? "").split("\n").map((l) => l.trim()).filter(Boolean).map((line) => {
      const m = line.match(/^([A-Za-z0-9][A-Za-z0-9_.-]*)=(.+)$/);
      const [rest, name] = m ? [m[2], m[1]] : [line, ""];
      const [url, branch = ""] = rest.split("#");
      return newRow({ url, branch, name: name || repoName(url) });
    });
    onChange([...rows, ...added]);
    setPaste(null);
  };
  return (
    <div className="stack tight">
      {rows.length === 0 && <span className="muted small">No custom repositories. Add them one by one, paste lines, or pick a profile.</span>}
      {rows.map((r) => (
        <div key={r.key} className="repo-row">
          <input className="mono" aria-label="Git URL" value={r.url} placeholder="git@example.com:org/repo.git" spellCheck={false}
            onChange={(e) => update(r.key, { url: e.target.value })} />
          <input className="mono" aria-label="Branch" value={r.branch} placeholder="branch (default)" spellCheck={false}
            onChange={(e) => update(r.key, { branch: e.target.value })} />
          <input className="mono" aria-label="Destination" value={r.destination} placeholder="custom/group/name" spellCheck={false}
            onChange={(e) => update(r.key, { destination: e.target.value, destTouched: true })} />
          <label className="check" title="Its modules go on the addons_path"><input type="checkbox" checked={r.addons} onChange={(e) => update(r.key, { addons: e.target.checked })} />addons</label>
          {r.origin && r.origin !== "wizard" && <Badge title="where this row came from">{r.origin.replace("profile:", "")}</Badge>}
          <button type="button" className="btn ghost icon sm" aria-label={`Remove ${r.name || "repository"}`} onClick={() => onChange(rows.filter((x) => x.key !== r.key))}><Trash2 /></button>
        </div>
      ))}
      <div className="row tight">
        <button type="button" className="btn sm" onClick={() => onChange([...rows, newRow()])}><Plus />Add repository</button>
        <button type="button" className="btn ghost sm" onClick={() => setPaste(paste === null ? "" : null)}><ClipboardPaste />Paste lines…</button>
      </div>
      {paste !== null && (
        <div className="stack tight">
          <textarea className="mono" rows={3} value={paste} autoFocus placeholder={"payroll=git@example.com:hr/payroll.git#staging-19\nhttps://github.com/OCA/web.git#19.0"} onChange={(e) => setPaste(e.target.value)} />
          <div className="row tight"><button type="button" className="btn sm" onClick={addPasted}>Add lines</button><span className="xs dim">one per line: [NAME=]URL[#BRANCH]</span></div>
        </div>
      )}
    </div>
  );
}

/** Destination folders under the root, drawn as a tree. */
export function DestTree({ root, rows }: { root: string; rows: { path: string; kind: string; label: string; addons: boolean }[] }) {
  const nodes = useMemo(() => {
    const seen = new Set<string>();
    const out: { depth: number; name: string; leaf?: { kind: string; label: string; addons: boolean } }[] = [];
    for (const r of [...rows].sort((a, b) => a.path.localeCompare(b.path))) {
      const parts = r.path.split("/");
      parts.forEach((part, n) => {
        const key = parts.slice(0, n + 1).join("/");
        if (seen.has(key)) return;
        seen.add(key);
        out.push({ depth: n, name: part, leaf: n === parts.length - 1 ? r : undefined });
      });
    }
    return out;
  }, [rows]);
  return (
    <div className="dest-tree mono xs" aria-label="Destination tree">
      <div className="dim">{root}/</div>
      {nodes.map((n, i) => (
        <div key={i} className="row tight" style={{ paddingLeft: 14 * (n.depth + 1) }}>
          <span>└─ {n.leaf ? <strong>{n.name}/</strong> : `${n.name}/`}</span>
          {n.leaf && <span className="dim">{n.leaf.kind} · {n.leaf.label}{n.leaf.kind !== "venv" && !n.leaf.addons ? " · not on addons_path" : ""}</span>}
        </div>
      ))}
    </div>
  );
}

function Origin({ of, plan }: { of: string; plan: ProvisionPlan | null }) {
  const o = plan?.profile?.origin?.[of];
  return o && o !== "built-in" ? <Badge title="layer this value came from">{o.replace("profile:", "")}</Badge> : null;
}

/** Provision wizard: setup, sources (with repositories), review (nothing changes), install and health. */
export function ProvisionDialog({ onClose }: { onClose: () => void }) {
  const app = useApp();
  const [stage, setStage] = useState<Stage>("setup");
  const [profiles, setProfiles] = useState<ProfileInfo[]>([]);
  const [org, setOrg] = useState<{ path: string; exists: boolean; error: string | null } | null>(null);
  const [profile, setProfile] = useState("");
  const [resolved, setResolved] = useState<ProfileResolved | null>(null);
  const [version, setVersion] = useState(17);
  const [install, setInstall] = useState<Install>(INSTALL_EMPTY);
  const [touched, setTouched] = useState<Set<keyof Install>>(new Set());
  const [enterprise, setEnterprise] = useState<"none" | "git" | "archive">("none");
  const [rows, setRows] = useState<RepoRow[]>([]);
  const [rowsTouched, setRowsTouched] = useState(false);
  const [plan, setPlan] = useState<ProvisionPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saveName, setSaveName] = useState("");
  const job = useJob<Done>("provision");

  useEffect(() => {
    rpc.request<{ profiles: ProfileInfo[]; org: typeof org }>("profile.list").then((r) => { setProfiles(r.profiles); setOrg(r.org); }).catch(() => undefined);
  }, []);
  // Profile or version changed: merge the layers again and show their values (fields you edited keep your value).
  useEffect(() => {
    setError(null);
    rpc.request<ProfileResolved>("profile.resolve", { profile: profile || null, version }).then((r) => {
      setResolved(r);
      if (r.pinned && r.pinned !== version) setVersion(r.pinned);
      setInstall((cur) => {
        const next = { ...cur };
        for (const k of Object.keys(INSTALL_EMPTY) as (keyof Install)[]) {
          if (!touched.has(k)) next[k] = r.install[k] !== undefined ? String(r.install[k]) : "";
        }
        return next;
      });
      if (!touched.has("enterprise_git") && !touched.has("enterprise_archive")) {
        setEnterprise(r.install.enterprise_git ? "git" : r.install.enterprise_archive ? "archive" : "none");
      }
      if (!rowsTouched) {
        setRows(r.repos.map((x, n) => newRow({
          name: x.name ?? "", url: x.url, branch: x.branch ?? "", destination: x.destination ?? "", destTouched: !!x.destination,
          addons: x.addons !== false, purpose: x.purpose ?? "custom", group: x.group ?? "", shallow: x.shallow !== false, origin: r.origin[`repos.${n}`],
        })));
      }
    }).catch((e) => setError(String(e.message)));
  }, [profile, version]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (k: keyof Install, v: string) => { setInstall((f) => ({ ...f, [k]: v })); setTouched((t) => new Set(t).add(k)); };
  const changeRows = (next: RepoRow[]) => { setRows(next); setRowsTouched(true); };

  const request = () => {
    const over: Record<string, unknown> = {};
    for (const k of touched) {
      if (k === "http_port") { if (install.http_port) over.http_port = Number(install.http_port); continue; }
      over[k] = install[k].trim();
    }
    // The chosen enterprise source wins over what a profile gave: the other kinds are cleared.
    if (enterprise !== "git") { over.enterprise_git = ""; over.enterprise_branch = ""; }
    if (enterprise !== "archive") over.enterprise_archive = "";
    const overrides: Record<string, unknown> = { install: over };
    if (rowsTouched) overrides.repos = rows.filter((r) => r.url.trim()).map(rowToRepo);
    return { profile: profile || null, version, overrides };
  };

  const browseArchive = async () => {
    setError(null);
    try {
      const { path } = await rpc.request<{ path: string | null }>("desktop.pickFile",
        { title: `Odoo ${version} Enterprise archive`, kind: "archive", start: install.enterprise_archive.trim() });
      if (path) set("enterprise_archive", path);
    } catch (e) {
      setError(String((e as Error).message));
    }
  };

  const review = async () => {
    setBusy(true); setError(null);
    try {
      setPlan(await rpc.request<ProvisionPlan>("provision.plan", request()));
      setStage("review");
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };
  const start = async () => {
    setBusy(true); setError(null);
    try {
      const r = await rpc.request<{ run_id: string }>("provision.run", request());
      job.begin(r.run_id);
      setStage("run");
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };
  const saveProfile = async () => {
    const req = request();
    const data: Record<string, unknown> = { name: saveName.trim(), odoo_version: version };
    const inst = Object.fromEntries(Object.entries(req.overrides.install as Record<string, unknown>).filter(([, v]) => v !== "" && v !== undefined));
    if (Object.keys(inst).length) data.install = inst;
    const repos = rows.filter((r) => r.url.trim()).map(rowToRepo);
    if (repos.length) data.repos = repos;
    await app.act("saving the profile", () => rpc.request("profile.save", { name: saveName.trim(), data }), `Saved profile ${saveName.trim()}`);
  };

  const close = () => { if (job.finished) { app.scan(); app.refresh(); } onClose(); };
  const reuse = !!plan?.preflight.some((c) => ["root-path", "run-as-user", "config-file"].includes(c.id) && c.status === "warn");
  const f = job.finished;
  const pinned = resolved?.pinned ?? null;
  const stageIndex = STAGES.findIndex((s) => s.id === stage);
  const missingUrl = rows.some((r) => !r.url.trim() && (r.branch || r.destTouched));

  const footer: ReactNode = stage === "setup" ? (
    <><button className="btn" onClick={close}>Cancel</button><button className="btn primary" onClick={() => setStage("sources")}>Sources<ArrowRight /></button></>
  ) : stage === "sources" ? (
    <><button className="btn" onClick={() => setStage("setup")}><ArrowLeft />Back</button>
      <button className="btn primary" disabled={busy || missingUrl} onClick={review}>{busy ? "Checking remotes…" : "Review"}</button></>
  ) : stage === "review" && plan ? (
    <><button className="btn" onClick={() => setStage("sources")}><ArrowLeft />Back</button>
      <button className="btn primary" disabled={busy || !plan.ok} onClick={start}>
        {plan.ok ? (reuse ? "Reuse and repair installation" : "Create installation") : "Fix the failed checks first"}
      </button></>
  ) : <button className="btn" disabled={job.busy} onClick={close}>Close</button>;

  return (
    <Dialog size="lg" icon={<PackagePlus />} title="New Odoo installation" onClose={close} locked={job.busy}
      subtitle={`Step ${stageIndex + 1} of 4 · ${STAGES[stageIndex].label}${stage === "review" ? ": nothing has changed yet" : ""}`} footer={footer}>
      <ol className="wizard-steps" aria-label="Steps">
        {STAGES.map((s, n) => <li key={s.id} aria-current={s.id === stage ? "step" : undefined} className={n < stageIndex ? "done" : undefined}>{s.label}</li>)}
      </ol>
      {error && <Callout tone="bad">{error}</Callout>}

      {stage === "setup" && (
        <>
          <Field label="Profile" hint={org?.exists ? <>Org overlay {org.error ? <span className="bad-text">has an error: {org.error}</span> : <>applies from <code>{org.path}</code></>}</> : "Saved defaults and repositories. Manage them in Settings."}>
            <select value={profile} onChange={(e) => {
              const pin = profiles.find((p) => p.name === e.target.value)?.odoo_version;
              if (pin) setVersion(pin);  // a pinned profile sets the version before the layers are merged
              setProfile(e.target.value); setRowsTouched(false);
            }}>
              <option value="">None (built-in defaults{org?.exists ? " + org overlay" : ""})</option>
              {profiles.map((p) => <option key={p.name} value={p.name} disabled={!!p.error}>{p.title ?? p.name}{p.odoo_version ? ` · Odoo ${p.odoo_version}` : ""} · {p.repos} repo(s){p.error ? " · invalid" : ""}</option>)}
            </select>
          </Field>
          <Field label={<>Odoo version {pinned && <Badge title="pinned by the profile"><Lock />{pinned}</Badge>}</>}>
            <Segmented label="Odoo version" value={String(version)} onChange={(v) => !pinned && setVersion(Number(v))}
              options={VERSIONS.map((v) => ({ value: String(v), label: String(v) }))} />
          </Field>
          <div className="grid2">
            <Field label="Installation folder" hint={`default /opt/odoo${version}`}>
              <input className="mono" value={install.root} placeholder={`/opt/odoo${version}`} onChange={(e) => set("root", e.target.value)} />
            </Field>
            <Field label="Linux user that runs Odoo" hint={`default odoo${version}`}>
              <input className="mono" value={install.run_as} placeholder={`odoo${version}`} onChange={(e) => set("run_as", e.target.value)} />
            </Field>
            <Field label="First config name">
              <input value={install.config_name} placeholder="default" onChange={(e) => set("config_name", e.target.value)} />
            </Field>
            <Field label="HTTP port" hint="optional">
              <input value={install.http_port} inputMode="numeric" className="mono" onChange={(e) => set("http_port", e.target.value.replace(/\D/g, ""))} />
            </Field>
          </div>
          <Disclosure title="Advanced: Python">
            <Field label="Python version" hint="Empty: the version tested with this Odoo release. A different one is your choice to verify.">
              <input className="mono" value={install.python} placeholder="default" onChange={(e) => set("python", e.target.value)} style={{ maxWidth: 200 }} />
            </Field>
          </Disclosure>
        </>
      )}

      {stage === "sources" && (
        <>
          <div className="grid2">
            <Field label="Community branch" hint={`default ${version}.0`}>
              <input value={install.odoo_branch} className="mono" placeholder={`${version}.0`} onChange={(e) => set("odoo_branch", e.target.value)} />
            </Field>
            <Field label="Community source" hint="a mirror or fork; default github.com/odoo/odoo">
              <input value={install.odoo_git} className="mono" placeholder="https://github.com/odoo/odoo.git" onChange={(e) => set("odoo_git", e.target.value)} />
            </Field>
          </div>
          <Field label="Enterprise source">
            <Segmented label="Enterprise source" value={enterprise} onChange={setEnterprise}
              options={[{ value: "none", label: "None (community only)" }, { value: "git", label: "Git URL" }, { value: "archive", label: "Local archive" }]} />
          </Field>
          {enterprise === "git" && (
            <div className="grid2">
              <Field label="Enterprise git URL" hint="your SSH key or credential helper must give access">
                <input className="mono" value={install.enterprise_git} placeholder="git@github.com:odoo/enterprise.git" onChange={(e) => set("enterprise_git", e.target.value)} />
              </Field>
              <Field label="Enterprise branch" hint={`default ${version}.0`}>
                <input className="mono" value={install.enterprise_branch} placeholder={`${version}.0`} onChange={(e) => set("enterprise_branch", e.target.value)} />
              </Field>
            </div>
          )}
          {enterprise === "archive" && (
            <Field label="Enterprise archive path" hint=".zip or .tar.*; checked during review">
              <div className="row tight">
                <input className="mono grow" value={install.enterprise_archive} placeholder="/home/you/enterprise-17.0.zip" onChange={(e) => set("enterprise_archive", e.target.value)} />
                <button type="button" className="btn" onClick={browseArchive}><FolderOpen />Browse…</button>
              </div>
            </Field>
          )}
          <div className="stack tight">
            <span className="label">Custom repositories <span className="dim xs">cloned as you, shallow; nested folders kept as typed</span></span>
            <RepoRows rows={rows} onChange={changeRows} />
          </div>
          {rows.length > 0 && <DestTree root={install.root || `/opt/odoo${version}`} rows={[
            { path: "odoo", kind: "community", label: install.odoo_branch || `${version}.0`, addons: true },
            ...(enterprise !== "none" ? [{ path: "enterprise", kind: "enterprise", label: enterprise === "archive" ? "archive" : install.enterprise_branch || `${version}.0`, addons: true }] : []),
            ...rows.filter((r) => r.destination).map((r) => ({ path: r.destination.replace(/^\/+|\/+$/g, ""), kind: r.purpose, label: r.branch || "default branch", addons: r.addons })),
          ]} />}
        </>
      )}

      {stage === "review" && plan && (
        <>
          {plan.previous && (
            <Callout tone={plan.previous.status === "complete" ? "info" : "warn"} title={`A previous run is ${plan.previous.status}`}>
              Last update {plan.previous.updated_at}. Finished: {plan.previous.completed.join(", ") || "nothing"}. Remaining: {plan.previous.remaining.join(", ") || "nothing"}.
              Finished parts are found and reused; nothing existing is overwritten.
            </Callout>
          )}
          <Callout tone="info">
            Creates or reuses user <b>{plan.spec.run_as}</b> and <code>{plan.spec.root}</code>. Existing files, configs and source trees are kept; a broken venv is
            moved aside and rebuilt. After you start, sudo asks for your password once.
          </Callout>
          <div className="stack tight">
            <span className="section-title">Values {plan.profile?.name && <Badge>{plan.profile.name}</Badge>}</span>
            <div className="kv-origin">
              {([["Odoo", `${plan.spec.version}`, "odoo_version"], ["Folder", plan.spec.root, "install.root"], ["Runs as", plan.spec.run_as, "install.run_as"],
                ["Python", plan.spec.python, "install.python"], ["Community", `${plan.spec.odoo_branch} · ${plan.spec.odoo_git}`, "install.odoo_branch"],
                ["Enterprise", plan.spec.enterprise_git ? `${plan.spec.enterprise_branch ?? `${version}.0`} · ${plan.spec.enterprise_git}` : plan.spec.enterprise_archive ?? "none", "install.enterprise_git"],
                ["Config", plan.spec.conf_path, "install.config_name"]] as [string, string, string][]).map(([k, v, o]) => (
                <div key={k} className="row tight"><span className="dim" style={{ width: 90 }}>{k}</span><span className="mono xs truncate grow">{v}</span><Origin of={o} plan={plan} /></div>
              ))}
            </div>
          </div>
          <div className="stack tight">
            <span className="section-title"><FolderTree style={{ width: 12, height: 12 }} /> Destination tree</span>
            <DestTree root={plan.spec.root} rows={plan.tree} />
          </div>
          <Disclosure title={`addons_path (${plan.addons_path.length})`}>
            <div className="stack tight">{plan.addons_path.map((a) => <code key={a} className="xs">{a}</code>)}</div>
            <span className="xs dim">Written into the config after cloning; a repository that is one module adds its parent folder.</span>
          </Disclosure>
          {!plan.remote_checked && <Callout tone="warn">Remote branches and access were not checked.</Callout>}
          <Checks checks={plan.preflight as Check[]} />
          <Steps steps={plan.steps as Step[]} actors />
          <Disclosure title="Script that runs as root (read before you start)"><pre className="block">{plan.root_script}</pre></Disclosure>
          <Disclosure title="Config that will be written"><pre className="block">{plan.config}</pre></Disclosure>
          <Disclosure title={<><BookmarkPlus style={{ width: 13, height: 13 }} /> Save these choices as a profile</>}>
            <div className="row tight">
              <input value={saveName} placeholder="profile name, e.g. education-19" onChange={(e) => setSaveName(e.target.value)} />
              <button className="btn sm" disabled={!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,60}$/.test(saveName.trim())} onClick={saveProfile}>Save</button>
            </div>
            <span className="xs dim">Stores the version, the values you changed and the repositories. Never passwords.</span>
          </Disclosure>
        </>
      )}
      {stage === "review" && !plan && <Loading />}

      {stage === "run" && (
        <JobView job={job}
          done={f && <>Odoo {version} is ready in <code>{f.root}</code>.</>}
          failed="Failed. Nothing is rolled back.">
          {f?.ok && (
            <Callout tone="ok" title="Installation health"
              action={<div className="row tight">
                <button className="btn sm" onClick={() => { app.scan(); onClose(); app.nav({ view: "installation", root: f.root, tab: "repos" }); }}><FolderGit2 />Repositories</button>
                <button className="btn sm" onClick={() => { onClose(); app.nav({ view: "doctor" }); app.runDoctor(); }}><Stethoscope />Run Doctor</button>
              </div>}>
              Verified: odoo-bin starts and the PostgreSQL role logs in. Config <code>{f.conf_path}</code>. Unlock <b>{f.run_as}</b> (Sessions → Agents) to start it.
            </Callout>
          )}
          {f && !f.ok && (
            <Callout tone="warn">
              Run it again: finished parts are reused and broken ones are repaired. The receipt <code>{f.root}/.odp-provision.json</code> shows the last finished phase,
              and the next review lists what remains.
            </Callout>
          )}
        </JobView>
      )}
    </Dialog>
  );
}
