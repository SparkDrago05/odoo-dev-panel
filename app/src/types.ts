// Shapes returned by the Python core. They mirror the dataclasses in core/src/odoo_dev_panel.

export type Agent = {
  user: string;
  state: "running" | "stopped" | "error";
  enabled: boolean;
  info?: { pid: number; started_at: string; running_sessions: number };
  error?: string;
};

export type Session = {
  id: string;
  user: string;
  name: string;
  argv: string[];
  pid: number;
  state: string;
  started_at: string;
  ended_at: string | null;
  exit_code: number | null;
  adopted: boolean;
  log_size: number;
  pty?: boolean;
  meta?: { kind?: string; db?: string | null; port?: number | null; instance?: string; preset?: string; debug?: { host: string; port: number; wait: boolean } };
};

export type GroupState = { group: string; exists: boolean; member: boolean; active: boolean };
export type AppInfo = { version: string; user: string; group?: GroupState };

export type Installation = {
  root: string; source: string; version: string | null; owner: string | null; venv_ok: boolean | null;
  venv?: string | null; venv_python?: string | null; venv_problem?: string | null; python_version?: string | null;
  venv_built_for?: string | null; configs?: string[]; home?: string | null;
  pg_role: string | null; adopted: boolean; name: string;
};
export type Instance = {
  path: string; name: string; installation: string | null; problems: string[]; version_hint: string | null;
  options?: Record<string, string>;
};
export type Database = { name: string; size: number; filestore: string | null; filestore_exists: boolean | null };
export type DbEntry = { installation: string; databases: Database[]; error: string | null };
export type Proc = {
  pid: number; user: string | null; port: number | null; instance: string | null; installation: string | null;
  database: string | null; unit?: string | null; argv?: string[];
};
export type Unit = {
  name: string; path?: string; active_state: string | null; sub_state: string | null; user: string | null;
  config: string | null; exec_start?: string; result?: string | null;
};
export type Snapshot = {
  installations: Installation[]; instances: Instance[]; databases: DbEntry[]; processes: Proc[]; units: Unit[];
  ports: { conflicts: { port: number; kind: string; pids: number[]; holder: number | null }[]; listening?: number[] };
  missing: { root: string; name: string }[];
  unreadable: string[];
  registry_error?: string;
};

export type Check = { id: string; status: "ok" | "warn" | "fail"; detail: string };
export type Step = { id: string; phase: number; actor: string; title: string; commands: string[] };
export type StepEvent = { run_id: string; step: string; status: "start" | "output" | "ok" | "fail"; text: string };

export type Finding = {
  check: string; code: string; severity: "error" | "warning" | "info"; subject: string; title: string;
  detail: string; why: string; commands: string[]; repair: string | null; install?: string[]; installation: string | null;
};
export type DoctorReport = { findings: Finding[]; counts: Record<string, number>; not_checked: string[] };

export type DbListing = { databases: Database[]; error: string | null; recipes: string[]; agent_running: boolean | null };
export type DbSnapshot = { path: string; name: string; database: string | null; created_at: string | null; bytes: number | null; filestore: boolean };
export type DbAction = "backup" | "restore" | "clone" | "drop" | "neutralize" | "snapshot" | "revert" | "forget";

export type Place = { container: string; host: string | null; volume: string | null; anonymous: boolean; in_image: boolean };
export type Container = {
  app_stack?: string | null;
  id: string; name: string; image: string; version: string | null; status: string | null; running: boolean;
  compose: { project: string; service: string | null; working_dir: string | null; files: string[] } | null;
  ports: { container: string; host_ip: string | null; host_port: number }[];
  config: Place | null; addons: Place[]; data: Place | null;
  db: { host: string | null; port: string | null; user: string | null; container: string | null };
};
export type DockerListing = { containers: Container[]; error: string | null; available: boolean; versions: string[]; stacks_root: string };

export type Service = {
  name: string; path: string; user: string | null; config: string | null; exec_start: string;
  active_state: string | null; sub_state: string | null; result: string | null; enabled: string | null;
};

// ---------- Git repositories (W1-W12) ----------

export type RepoProblem = {
  code: string; level: "info" | "warn" | "error"; title: string; detail: string; commands: string[]; heuristic: boolean;
};
export type RepoState = {
  path: string; ok: boolean; owner: string | null; foreign: boolean; branch: string | null; head: string | null;
  detached: boolean; upstream: string | null; ahead: number | null; behind: number | null; staged: number;
  modified: number; untracked: number; conflicted: number; shallow: boolean; worktree: boolean;
  remotes: Record<string, string>; subject: string | null; committed: string | null; error: string | null;
  problems: RepoProblem[]; dirty: boolean;
};
export type RepoAssoc = {
  purpose?: string; addons?: boolean; preferred_branch?: string; expected_version?: string; destination?: string;
  group?: string; bulk?: boolean;
};
export type RepoLink = {
  root: string; relative: string | null; sources: string[]; assoc: RepoAssoc | null; alignment: RepoProblem | null;
};
export type Repo = {
  path: string; real: string; name: string; gitdir: string | null; purpose: string; registered: boolean;
  missing?: boolean; installations: RepoLink[]; state: RepoState | null;
};
export type RepoOp = "fetch" | "pull" | "switch" | "checkout" | "clone" | "bundle";
export type RepoPlanItem = { repo: string; title: string; commands: string[]; skip: string | null; problems: RepoProblem[]; level: "ok" | "warn" | "fail" };
export type RepoPlan = {
  op: RepoOp; ok: boolean; items: RepoPlanItem[]; checks: Check[]; steps: Step[];
  counts: { run: number; skip: number };
};
export type RepoResult = {
  repo: string; commands: string[]; status: "ok" | "kept" | "failed" | "skipped" | "cancelled"; output: string;
  problem: RepoProblem | null; reason: string | null; changed_files?: number; changed_modules?: string[];
  addons_path_entry?: string | null;
};
export type RepoDiff = {
  repo: string; files: { path: string; index: string; worktree: string }[]; truncated: boolean;
  diff: string | null; diff_truncated?: boolean;
  incoming: { sha: string; author: string; date: string; subject: string }[];
  outgoing: { sha: string; author: string; date: string; subject: string }[];
};

// ---------- Profiles and provision plans (T1-T7) ----------

export type ProfileInfo = {
  name: string; path: string; error: string | null; title: string | null; description: string | null;
  odoo_version: number | null; repos: number; has_install: boolean;
};
export type ProfileRepo = {
  name?: string; url: string; branch?: string; destination?: string; addons?: boolean; purpose?: string; group?: string; shallow?: boolean;
};
export type ProfileResolved = {
  version: number; pinned: number | null; install: Record<string, string | number>; config: Record<string, string>;
  repos: ProfileRepo[]; origin: Record<string, string>; root: string; run_as: string; name: string | null; description: string | null;
};
export type ProvisionPlan = {
  spec: {
    version: number; run_as: string; root: string; python: string; odoo_git: string; odoo_branch: string; enterprise_git: string | null;
    enterprise_branch: string | null; enterprise_archive: string | null; conf_path?: string; config_name: string;
  } & Record<string, unknown>;
  preflight: Check[]; ok: boolean; steps: Step[]; root_script: string; config: string; addons_path: string[];
  tree: { path: string; kind: string; label: string; addons: boolean; group?: string | null; name?: string }[];
  profile: { name: string | null; description: string | null; origin: Record<string, string>; pinned: number | null; version: number } | null;
  previous: { path: string; status: string; last_phase: string; updated_at: string; completed: string[]; remaining: string[] } | null;
  remote_checked: boolean;
};
export type AddonsProposal = {
  path: string; current: string[]; add: string[]; after?: string[]; sha: string | null; writable?: boolean; error: string | null;
};

// ---------- Module center (M1-M9) ----------

export type ModuleProblem = { code: string; level: "error" | "warn" | "info"; text: string };
export type ModuleChange = {
  name: string; path: string; repo: string | null; files: { path: string; status: string; kind: string }[]; kinds: string[];
  new: boolean; removed: boolean; dependency_changed: string[]; action: string; why: string;
};
export type ModuleInfo = {
  path: string; addons_path: string; depends: string[]; required_by: string[]; installable: boolean;
  name?: string; version?: string; repo?: string | null; loadable: boolean; own: boolean; problems: ModuleProblem[];
  db_state?: string | null; db_version?: string | null; version_differs?: boolean; change?: ModuleChange;
};
export type ModuleCenter = {
  modules: Record<string, ModuleInfo>; missing: Record<string, string[]>; cycles: string[][]; addons_paths: string[];
  installation: string | null; series: string | null; config: string; instance: string; repos: string[];
  database: string | null; db_error: string | null;
  changed: { modules: Record<string, ModuleChange>; errors: Record<string, string>; heuristic: boolean };
};
export type ModulePlan = {
  kind: "upgrade" | "install" | "test"; config: string; database: string; modules: string[]; checks: Check[]; steps: Step[];
  ok: boolean; argv: string[]; user: string; snapshot?: boolean; tags?: string | null; demo?: boolean;
};
export type ModuleTestRun = {
  id: string; at: string; installation: string; config: string; modules: string[]; tags: string | null; demo: boolean;
  database: string; kept: boolean; exit_code: number | null; status: string; tests: number; failures: number; errors: number;
  failed: { kind: string; test: string }[]; note: string | null;
};

// ---------- Python environment (Y1-Y9) ----------

export type PyRequirement = {
  file: string; name: string; raw: string; spec: string; marker: string | null; installed: string | null; installed_as: string | null;
  status: "ok" | "missing" | "mismatch" | "not-applicable" | "unknown" | "manifest-missing"; detail: string;
};
export type PyReqFile = { path: string; state: "used" | "added" | "available"; count: number };
export type PyEnv = {
  root: string; version: string | null; run_as: string | null;
  interpreter: {
    venv: string | null; python: string | null; target: string | null; version: string | null; built_for: string | null; pinned: string | null;
    uv_managed: boolean; system_python: boolean; problem: string | null; matches_pin: boolean | null;
  };
  files: string[]; detected: PyReqFile[]; requirements: PyRequirement[]; counts: Record<"ok" | "missing" | "mismatch" | "not-applicable" | "unknown" | "manifest-missing", number>;
  conflicts: { name: string; reason: string; asked: { file: string; spec: string }[] }[]; packages: Record<string, string>; extras: string[];
};
export type PyTool = { tool: string; scope: "venv" | "system"; installed: boolean; version: string | null; detail: string; patched?: boolean };
export type PyDisk = { root: string; parts: { label: string; path: string; bytes: number; complete: boolean }[]; free: number; total: number };
export type PyPlan = { kind: string; ok: boolean; checks: Check[]; steps: Step[]; packages?: string[]; script?: string; tool?: string; argv?: string[] };
