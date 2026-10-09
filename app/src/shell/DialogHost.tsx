import { CompareDialog } from "../features/Compare";
import { ConfigEditorDialog, FixPermissionsDialog } from "../features/ConfigEditor";
import { DbActionDialog } from "../features/DbAction";
import { DeleteStackDialog, DockerActionDialog, DockerLogsDialog, DockerShellDialog, NewStackDialog } from "../features/DockerDialogs";
import { ModuleActionDialog, ScaffoldDialog } from "../features/ModuleCenter";
import { ModulesDialog } from "../features/Modules";
import { PythonActionDialog } from "../features/PythonEnv";
import { ConfirmDialog, PasswordDialog } from "../features/Prompts";
import { ProvisionDialog } from "../features/Provision";
import { RepairVenvDialog } from "../features/RepairVenv";
import { ApplyProfileDialog, ExportProfileDialog } from "../features/Profiles";
import { RepoAddDialog, RepoDiffDialog, RepoOpDialog } from "../features/RepoDialogs";
import { RunDialog } from "../features/Run";
import { useApp } from "../state/app";

/** Dialogs any view or the command palette can open, plus the sudo password and confirmation prompts. */
export function DialogHost() {
  const app = useApp();
  const d = app.dialog;
  const close = () => app.setDialog(null);
  const done = (changed: boolean, ...keys: string[]) => {
    close();
    if (changed) app.invalidate(...keys);
  };
  return (
    <>
      {d?.kind === "provision" && <ProvisionDialog onClose={close} />}
      {d?.kind === "config" && <ConfigEditorDialog path={d.path} root={d.root} onClose={(changed) => { done(changed, "config"); if (changed) app.scan(); }} />}
      {d?.kind === "modules" && <ModulesDialog path={d.path} onClose={close} />}
      {d?.kind === "compare" && <CompareDialog path={d.path} mode={d.mode} onClose={close} />}
      {d?.kind === "db" && <DbActionDialog root={d.root} action={d.action} source={d.source} backup={d.backup} onClose={(changed) => done(changed, "db")} />}
      {d?.kind === "run" && <RunDialog instance={d.instance} onClose={close} />}
      {d?.kind === "repair-venv" && <RepairVenvDialog root={d.root} onClose={(changed) => { done(changed, "doctor"); if (changed) { app.scan(); app.runDoctor(); } }} />}
      {d?.kind === "fix-perms" && <FixPermissionsDialog root={d.root} onClose={(applied) => { done(applied, "config"); if (applied) app.runDoctor(); }} />}
      {d?.kind === "docker-new" && <NewStackDialog onClose={(changed) => done(changed, "docker")} />}
      {d?.kind === "docker-action" && <DockerActionDialog container={d.container} action={d.action} onClose={(changed) => done(changed, "docker")} />}
      {d?.kind === "docker-logs" && <DockerLogsDialog container={d.container} onClose={close} onError={app.onError} />}
      {d?.kind === "docker-shell" && <DockerShellDialog container={d.container} onClose={close} onError={app.onError} />}
      {d?.kind === "repo-op" && <RepoOpDialog op={d.op} repos={d.repos} bulk={d.bulk} installation={d.installation} onClose={(changed) => done(changed, "repos")} />}
      {d?.kind === "repo-add" && <RepoAddDialog installation={d.installation} onClose={(changed) => done(changed, "repos")} />}
      {d?.kind === "repo-diff" && <RepoDiffDialog path={d.path} onClose={close} />}
      {d?.kind === "module-action" && <ModuleActionDialog action={d.action} config={d.config} database={d.database} modules={d.modules} onClose={(changed) => done(changed, "modules")} />}
      {d?.kind === "python-action" && <PythonActionDialog root={d.root} params={d.params} onClose={(changed) => { done(changed, "python"); if (changed) app.scan(); }} />}
      {d?.kind === "module-scaffold" && <ScaffoldDialog installation={d.installation} onClose={(changed) => done(changed, "modules")} />}
      {d?.kind === "profile-export" && <ExportProfileDialog root={d.root} onClose={close} />}
      {d?.kind === "profile-apply" && <ApplyProfileDialog installation={d.installation} onClose={(changed) => done(changed, "repos")} />}
      {d?.kind === "docker-delete" && <DeleteStackDialog container={d.container} onClose={(changed) => done(changed, "docker", "db")} />}

      {app.confirmReq && <ConfirmDialog request={app.confirmReq} onDone={(ok) => { app.confirmReq!.resolve(ok); app.setConfirmReq(null); }} />}
      {app.password && <PasswordDialog request={app.password} onDone={(pw) => { app.password!.resolve(pw); app.setPassword(null); }} />}
    </>
  );
}
