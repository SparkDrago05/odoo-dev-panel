import { Wrench } from "lucide-react";
import { useEffect, useState } from "react";
import { rpc } from "../rpc";
import type { Check, Step } from "../types";
import { Dialog } from "../ui/Dialog";
import { Checks, type Finished, JobView, Steps, useJob } from "../ui/Job";
import { Callout, CheckBox, Loading } from "../ui/primitives";

type Plan = {
  root: string; version: string; run_as: string; python: string; venv: string; requirements: string[];
  extras: string[]; carry_extras: boolean; checks: Check[]; steps: Step[]; ok: boolean;
};
type Done = Finished & { venv: string; backup?: string | null };

/** Rebuild a venv next to the old one, validate it, then swap. The old venv is never touched before the swap. */
export function RepairVenvDialog({ root, onClose }: { root: string; onClose: (changed: boolean) => void }) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [custom, setCustom] = useState(true);
  const [carry, setCarry] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const job = useJob<Done>("repair");

  useEffect(() => {
    setError(null);
    rpc.request<Plan>("repair.plan", { root, custom, carry_extras: carry }).then(setPlan).catch((e) => setError(String(e.message)));
  }, [root, custom, carry]);

  const start = async () => {
    setError(null);
    try {
      job.begin((await rpc.request<{ run_id: string }>("repair.run", { root, custom, carry_extras: carry })).run_id);
    } catch (e) {
      setError(String((e as Error).message));
    }
  };
  const f = job.finished;

  return (
    <Dialog size="lg" icon={<Wrench />} title="Rebuild the venv" subtitle={root} onClose={() => onClose(!!f)} locked={job.busy}
      footer={<>
        <button className="btn" disabled={job.busy} onClick={() => onClose(!!f)}>{job.started ? "Close" : "Cancel"}</button>
        {!job.started && plan && <button className="btn primary" disabled={!plan.ok} onClick={start}><Wrench />{plan.ok ? "Rebuild venv" : "Fix the failed checks first"}</button>}
      </>}>
      {error && <Callout tone="bad">{error}</Callout>}
      {!job.started && !plan && !error && <Loading>Checking…</Loading>}
      {!job.started && plan && (
        <>
          <Callout tone="info">
            Builds a new venv with Python {plan.python} next to the old one, as {plan.run_as}, and validates it. Only then the old venv is renamed to
            venv.bak-&lt;time&gt; and the new one takes its place. If anything fails before that, the old venv was never touched. Nothing is deleted.
          </Callout>
          <Checks checks={plan.checks} />
          <div className="stack tight">
            <CheckBox checked={custom} onChange={setCustom}>Install the requirements.txt files of custom repositories</CheckBox>
            <span className="xs dim mono break" style={{ paddingLeft: 22 }}>{plan.requirements.join(", ") || "no requirement files"}</span>
          </div>
          {plan.extras.length > 0 && (
            <div className="stack tight">
              <CheckBox checked={carry} onChange={setCarry}>
                Also install the {plan.extras.length} package(s) of the old venv that no requirements file names (latest compatible versions)
              </CheckBox>
              <span className="xs dim mono break" style={{ paddingLeft: 22 }}>{plan.extras.join(" ")}</span>
            </div>
          )}
          <Steps steps={plan.steps} />
        </>
      )}
      {job.started && (
        <JobView job={job} failed="Failed. The old venv is in service.">
          {f?.ok && <Callout tone="ok"><code>{f.venv}</code> is rebuilt.{f.backup ? <> The old venv is kept as <code>{f.backup}</code>; remove it when you no longer need it.</> : ""}</Callout>}
        </JobView>
      )}
    </Dialog>
  );
}
