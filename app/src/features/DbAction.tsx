import { Archive, Camera, Copy, Eraser, History, RotateCcw, Trash2, Upload } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { rpc } from "../rpc";
import type { Check, DbAction, DbListing, Step } from "../types";
import { Dialog } from "../ui/Dialog";
import { Checks, type Finished, JobView, Steps, useJob } from "../ui/Job";
import { Callout, CheckBox, Field, Loading } from "../ui/primitives";

type Plan = { kind: string; root: string; run_as: string; checks: Check[]; steps: Step[]; ok: boolean; recipe_sum: string | null };
type Params = { source?: string; target?: string; backup?: string; dest?: string; recipe?: string; confirm?: string; keep?: number };
type Done = Finished & { receipt?: string; backup?: string; trash?: string; aside?: string; aside_fs?: string | null; pruned?: string[] };

export const DB_ACTION: Record<DbAction, { title: string; icon: ReactNode; destructive?: boolean }> = {
  backup: { title: "Back up", icon: <Archive /> },
  restore: { title: "Restore a backup", icon: <Upload /> },
  clone: { title: "Clone", icon: <Copy /> },
  snapshot: { title: "Snapshot", icon: <Camera /> },
  neutralize: { title: "Neutralize", icon: <Eraser />, destructive: true },
  drop: { title: "Drop", icon: <Trash2 />, destructive: true },
  revert: { title: "Revert to snapshot", icon: <RotateCcw />, destructive: true },
  forget: { title: "Delete snapshot", icon: <History /> },
};

/** One database job: options, the core's plan (checks and exact steps), typed confirmation for destructive actions,
 * then the live log. Nothing runs before the plan's checks pass. */
export function DbActionDialog({ root, action, source, backup: initialBackup, onClose }: {
  root: string; action: DbAction; source?: string; backup?: string; onClose: (changed: boolean) => void;
}) {
  const [recipes, setRecipes] = useState<string[] | null>(null);
  const [target, setTarget] = useState("");
  const [backup, setBackup] = useState(initialBackup ?? "");
  const [keep, setKeep] = useState("3");
  const [dest, setDest] = useState("");
  const [recipe, setRecipe] = useState("default");
  const [neutralize, setNeutralize] = useState(false);
  const [confirm, setConfirm] = useState("");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const job = useJob<Done>("db");

  useEffect(() => {
    rpc.request<DbListing>("db.list", { root }).then((l) => setRecipes(l.recipes)).catch(() => setRecipes([]));
  }, [root]);

  const params = (): Params => ({
    source, target: target || undefined, backup: backup || undefined, dest: dest || undefined,
    recipe: action === "neutralize" || (action === "clone" && neutralize) ? recipe : undefined,
    confirm: confirm || undefined,
    keep: action === "snapshot" && keep ? Number(keep) : undefined,
  });

  useEffect(() => {
    const timer = setTimeout(() => {
      setError(null);
      rpc.request<Plan>("db.plan", { root, action, ...params() }).then(setPlan).catch((e) => setError(String(e.message)));
    }, 300);
    return () => clearTimeout(timer);
  }, [root, action, source, target, backup, dest, recipe, neutralize, confirm, keep]); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    setError(null);
    try {
      job.begin((await rpc.request<{ run_id: string }>("db.run", { root, action, ...params() })).run_id);
    } catch (e) {
      setError(String((e as Error).message));
    }
  };

  const meta = DB_ACTION[action];
  const destructive = !!meta.destructive;
  // Typed confirmation is its own field, so it is not reported as a failed check while the user is still typing.
  const hidden = destructive && !confirm ? "confirm" : "";
  const f = job.finished;
  const where = root.startsWith("docker:") ? `container ${root.slice(7)}` : root;

  return (
    <Dialog size="lg" icon={meta.icon} title={`${meta.title}${source ? ` ${source}` : ""}`} subtitle={where} onClose={() => onClose(!!f)} locked={job.busy}
      footer={<>
        <button className="btn" disabled={job.busy} onClick={() => onClose(!!f)}>{job.started ? "Close" : "Cancel"}</button>
        {!job.started && (
          <button className={`btn ${destructive ? "danger solid" : "primary"}`} disabled={!plan?.ok} onClick={start}>
            {meta.icon}{plan?.ok ? meta.title : destructive && !confirm ? `Type ${source} to confirm` : "Fix the failed checks first"}
          </button>
        )}
      </>}>
      {error && <Callout tone="bad">{error}</Callout>}
      {!job.started && (
        <>
          {action === "clone" && (
            <>
              <Field label="New database name"><input className="mono" autoFocus value={target} onChange={(e) => setTarget(e.target.value)} /></Field>
              <CheckBox checked={neutralize} onChange={setNeutralize}>Run a neutralization recipe on the clone</CheckBox>
            </>
          )}
          {action === "backup" && (
            <Field label="Folder for backups" hint="empty: the run-as user's home/odp-backups"><input className="mono" value={dest} onChange={(e) => setDest(e.target.value)} /></Field>
          )}
          {action === "restore" && (
            <>
              <Field label="Backup folder" hint="absolute path"><input className="mono" autoFocus={!initialBackup} value={backup} onChange={(e) => setBackup(e.target.value)} /></Field>
              <Field label="New database name"><input className="mono" value={target} onChange={(e) => setTarget(e.target.value)} /></Field>
            </>
          )}
          {(action === "neutralize" || (action === "clone" && neutralize)) && (
            <Field label="Recipe">
              {recipes === null ? <Loading>Reading recipes…</Loading> : (
                <select value={recipe} onChange={(e) => setRecipe(e.target.value)}>{recipes.map((r) => <option key={r}>{r}</option>)}</select>
              )}
            </Field>
          )}
          {action === "snapshot" && (
            <>
              <p className="muted">A backup in the run-as user's home/odp-backups/snapshots. Older snapshots of {source} beyond the number kept are removed; manual backups are never touched.</p>
              <Field label={`Snapshots of ${source} to keep`}><input className="mono" style={{ width: 80 }} value={keep} onChange={(e) => setKeep(e.target.value.replace(/\D/g, ""))} /></Field>
            </>
          )}
          {action === "revert" && (
            <Callout tone="warn">
              <b>{source}</b> is replaced by the snapshot <code>{initialBackup}</code>. Nothing is deleted: the current {source} and its filestore are kept under
              a new name (shown in the checks); drop them when you no longer need them.
            </Callout>
          )}
          {action === "forget" && <Callout tone="warn">The snapshot folder <code>{initialBackup}</code> is removed. This cannot be undone.</Callout>}
          {action === "drop" && <Callout tone="bad">The database is dropped. Its filestore is moved to a <code>.trash-…</code> folder next to it, not deleted.</Callout>}
          {action === "neutralize" && (
            <Callout tone="warn">This changes the data of <b>{source}</b> in place (passwords, schedules, mail servers). Clone first if this is a client database.</Callout>
          )}
          {destructive && (
            <Field label={<>Type <b className="mono" style={{ color: "var(--text)" }}>{source}</b> to confirm</>}>
              <input className="mono" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="off" spellCheck={false} autoFocus={action !== "clone"} />
            </Field>
          )}
          {!plan && !error && <Loading>Checking…</Loading>}
          {plan && (
            <>
              <Checks checks={plan.checks.filter((c) => c.id !== hidden)} />
              <Steps steps={plan.steps} />
            </>
          )}
        </>
      )}
      {job.started && (
        <JobView job={job}>
          {f?.ok && f.backup && <Callout tone="ok">Backup: <code>{f.backup}</code></Callout>}
          {f?.ok && f.trash && <Callout tone="info">Filestore moved to <code>{f.trash}</code>. Remove it when you no longer need it.</Callout>}
          {f?.ok && f.aside && <Callout tone="info">The previous database is kept as <code>{f.aside}</code>{f.aside_fs ? <>, its filestore as <code>{f.aside_fs}</code></> : ""}.</Callout>}
          {f?.ok && f.pruned && f.pruned.length > 0 && <p className="muted small">Older snapshots removed: {f.pruned.join(", ")}</p>}
        </JobView>
      )}
    </Dialog>
  );
}
