// Development-only fake core. `pnpm dev` in a plain browser (no Tauri) answers RPC calls from these fixtures,
// so the UI can be rendered and checked without touching real installations, databases or services.
// Nothing here is bundled into production builds (see rpc.ts).

/* eslint-disable @typescript-eslint/no-explicit-any */
type Json = any;
type Deliver = (message: Json) => void;

const now = Date.now();
const iso = (minutesAgo: number) => new Date(now - minutesAgo * 60_000).toISOString();

const installations = [
  { root: "/opt/odoo17", source: "/opt/odoo17/odoo", version: "17.0", owner: "odoo17", venv: "/opt/odoo17/venv", venv_python: "/opt/odoo17/venv/bin/python", venv_ok: true, venv_problem: null, python_version: "3.10", venv_built_for: "3.10", pg_role: "odoo17", configs: [], home: "/opt/odoo17", adopted: false, name: "odoo17" },
  { root: "/opt/odoo18", source: "/opt/odoo18/odoo", version: "18.0", owner: "odoo18", venv: "/opt/odoo18/venv", venv_python: "/opt/odoo18/venv/bin/python", venv_ok: false, venv_problem: "built for Python 3.11, bin/python is now 3.13", python_version: "3.13", venv_built_for: "3.11", pg_role: "odoo18", configs: [], home: "/opt/odoo18", adopted: false, name: "odoo18" },
  { root: "/opt/odoo19", source: "/opt/odoo19/odoo", version: "19.0", owner: "odoo19", venv: "/opt/odoo19/venv", venv_python: "/opt/odoo19/venv/bin/python", venv_ok: true, venv_problem: null, python_version: "3.12", venv_built_for: "3.12", pg_role: "odoo19", configs: [], home: "/opt/odoo19", adopted: true, name: "main19" },
  { root: "/srv/odoo16-legacy", source: "/srv/odoo16-legacy/server", version: "16.0", owner: "odoo16", venv: "/srv/odoo16-legacy/venv", venv_python: "/srv/odoo16-legacy/venv/bin/python", venv_ok: true, venv_problem: null, python_version: "3.10", venv_built_for: "3.10", pg_role: "odoo16", configs: [], home: "/srv/odoo16-legacy", adopted: false, name: "odoo16-legacy" },
];
const opts = (port: number, db: string, root: string) => ({ http_port: String(port), db_name: db, db_user: root.split("/").pop()!, addons_path: `${root}/odoo/addons,${root}/enterprise,${root}/custom` });
const instances: any[] = [
  { path: "/etc/odoo/odoo17/acme.conf", name: "acme", installation: "/opt/odoo17", problems: [], version_hint: "17.0", options: opts(8069, "acme_prod", "/opt/odoo17") },
  { path: "/etc/odoo/odoo17/demo.conf", name: "demo", installation: "/opt/odoo17", problems: [], version_hint: "17.0", options: opts(8070, "False", "/opt/odoo17") },
  { path: "/etc/odoo/odoo18/staging.conf", name: "staging", installation: "/opt/odoo18", problems: ["2 addons_path entries missing: /opt/odoo18/custom/hr, /opt/odoo18/custom/payroll"], version_hint: "18.0", options: opts(8080, "staging", "/opt/odoo18") },
  { path: "/etc/odoo/odoo19/nutech.conf", name: "nutech", installation: "/opt/odoo19", problems: [], version_hint: "19.0", options: opts(8072, "nutech_prod", "/opt/odoo19") },
  { path: "/etc/odoo/odoo19/ksbl.conf", name: "ksbl", installation: "/opt/odoo19", problems: [], version_hint: "19.0", options: opts(8073, "ksbl", "/opt/odoo19") },
  { path: "/etc/odoo/odoo19/sandbox-with-a-rather-long-config-name.conf", name: "sandbox-with-a-rather-long-config-name", installation: "/opt/odoo19", problems: [], version_hint: "19.0", options: opts(8074, "False", "/opt/odoo19") },
  { path: "/srv/odoo16-legacy/odoo.conf", name: "odoo", installation: "/srv/odoo16-legacy", problems: [], version_hint: "16.0", options: opts(8069, "legacy", "/srv/odoo16-legacy") },
  { path: "/etc/odoo/odoo13/old-client.conf", name: "old-client", installation: null, problems: ["no installation for Odoo 13"], version_hint: "13.0", options: {} },
];
const fs = (root: string, db: string) => `${root}/.local/share/Odoo/filestore/${db}`;
const dbs: Record<string, { name: string; size: number; filestore: string | null; filestore_exists: boolean | null }[]> = {
  "/opt/odoo17": [
    { name: "acme_prod", size: 1_840_000_000, filestore: fs("/opt/odoo17", "acme_prod"), filestore_exists: true },
    { name: "acme_test_2026_10", size: 1_790_000_000, filestore: fs("/opt/odoo17", "acme_test_2026_10"), filestore_exists: true },
    { name: "demo17", size: 96_000_000, filestore: fs("/opt/odoo17", "demo17"), filestore_exists: false },
  ],
  "/opt/odoo18": [{ name: "staging", size: 420_000_000, filestore: fs("/opt/odoo18", "staging"), filestore_exists: true }],
  "/opt/odoo19": [
    { name: "nutech_prod", size: 3_200_000_000, filestore: fs("/opt/odoo19", "nutech_prod"), filestore_exists: true },
    { name: "nutech_upgrade_test", size: 3_150_000_000, filestore: fs("/opt/odoo19", "nutech_upgrade_test"), filestore_exists: true },
    { name: "ksbl", size: 610_000_000, filestore: null, filestore_exists: null },
  ],
  "/srv/odoo16-legacy": [{ name: "legacy", size: 230_000_000, filestore: fs("/srv/odoo16-legacy", "legacy"), filestore_exists: true }],
  "docker:shop18": [{ name: "shop", size: 140_000_000, filestore: "/var/lib/odoo/filestore/shop", filestore_exists: true }],
};
const snapshots: Record<string, any[]> = {
  "/opt/odoo19": [
    { path: "/opt/odoo19/odp-backups/snapshots/nutech_prod-20261007-1810", name: "nutech_prod-20261007-1810", database: "nutech_prod", created_at: iso(60 * 20), bytes: 2_900_000_000, filestore: true },
    { path: "/opt/odoo19/odp-backups/snapshots/nutech_prod-20261008-0915", name: "nutech_prod-20261008-0915", database: "nutech_prod", created_at: iso(140), bytes: 2_910_000_000, filestore: true },
  ],
};

let agents: any[] = [
  { user: "odoo16", state: "stopped", enabled: false },
  { user: "odoo17", state: "running", enabled: true, info: { pid: 4120, started_at: iso(300), running_sessions: 0 } },
  { user: "odoo18", state: "stopped", enabled: true },
  { user: "odoo19", state: "running", enabled: true, info: { pid: 4188, started_at: iso(290), running_sessions: 1 } },
];
const venv = (root: string) => [`${root}/venv/bin/python`, `${root === "/srv/odoo16-legacy" ? root + "/server" : root + "/odoo"}/odoo-bin`];
let sessions: any[] = [
  { id: "a1b2c3", user: "odoo19", name: "nutech", argv: [...venv("/opt/odoo19"), "-c", "/etc/odoo/odoo19/nutech.conf", "-d", "nutech_prod", "--http-port", "8072", "--dev=reload,xml"], pid: 51234, state: "running", started_at: iso(42), ended_at: null, exit_code: null, adopted: false, log_size: 120_000, meta: { kind: "server", db: "nutech_prod", port: 8072, instance: "/etc/odoo/odoo19/nutech.conf" } },
  { id: "d4e5f6", user: "odoo17", name: "acme -u sale_custom", argv: [...venv("/opt/odoo17"), "-c", "/etc/odoo/odoo17/acme.conf", "-d", "acme_test_2026_10", "-u", "sale_custom", "--stop-after-init"], pid: 50110, state: "exited", started_at: iso(95), ended_at: iso(93), exit_code: 0, adopted: false, log_size: 40_000, meta: { kind: "upgrade", db: "acme_test_2026_10", port: null, instance: "/etc/odoo/odoo17/acme.conf" } },
  { id: "g7h8i9", user: "odoo17", name: "acme", argv: [...venv("/opt/odoo17"), "-c", "/etc/odoo/odoo17/acme.conf", "-d", "acme_prod", "--http-port", "8069"], pid: 49001, state: "exited", started_at: iso(200), ended_at: iso(150), exit_code: 1, adopted: true, log_size: 90_000, meta: { kind: "server", db: "acme_prod", port: 8069, instance: "/etc/odoo/odoo17/acme.conf" } },
  { id: "j1k2l3", user: "odoo19", name: "shell ksbl", argv: [...venv("/opt/odoo19"), "shell", "--shell-interface=python", "-c", "/etc/odoo/odoo19/ksbl.conf", "-d", "ksbl"], pid: 50500, state: "exited", started_at: iso(400), ended_at: iso(380), exit_code: 0, adopted: false, log_size: 3_000, pty: true, meta: { kind: "shell", db: "ksbl", port: null, instance: "/etc/odoo/odoo19/ksbl.conf" } },
];

const LOG = (db: string) => `2026-10-08 09:00:01,101 51234 INFO ? odoo: Odoo version 19.0
2026-10-08 09:00:01,102 51234 INFO ? odoo: Using configuration file at /etc/odoo/odoo19/nutech.conf
2026-10-08 09:00:01,103 51234 INFO ? odoo: addons paths: ['/opt/odoo19/odoo/addons', '/opt/odoo19/enterprise', '/opt/odoo19/custom']
2026-10-08 09:00:01,104 51234 INFO ? odoo: database: odoo19@default:default
2026-10-08 09:00:01,410 51234 INFO ? odoo.service.server: HTTP service (werkzeug) running on localhost:8072
2026-10-08 09:00:02,012 51234 INFO ${db} odoo.modules.loading: loading 1 modules...
2026-10-08 09:00:02,140 51234 INFO ${db} odoo.modules.loading: 1 modules loaded in 0.13s, 0 queries (+0 extra)
2026-10-08 09:00:03,880 51234 INFO ${db} odoo.modules.loading: loading 214 modules...
2026-10-08 09:00:07,233 51234 WARNING ${db} odoo.modules.loading: The models ['x_legacy.report'] have no access rules in module nutech_hr, consider adding some
2026-10-08 09:00:07,400 51234 INFO ${db} odoo.modules.loading: 214 modules loaded in 3.52s, 0 queries (+0 extra)
2026-10-08 09:00:07,610 51234 INFO ${db} odoo.modules.registry: Registry loaded in 5.598s
2026-10-08 09:02:11,045 51234 INFO ${db} werkzeug: 127.0.0.1 - - [08/Oct/2026 09:02:11] "GET /odoo/action-sale.action_orders HTTP/1.1" 200 - 41 0.031 0.140
2026-10-08 09:04:52,733 51234 ERROR ${db} odoo.http: Exception during request handling.
Traceback (most recent call last):
  File "/opt/odoo19/odoo/odoo/http.py", line 2154, in __call__
    response = request._serve_db()
  File "/opt/odoo19/custom/nutech_sale/models/sale_order.py", line 88, in _compute_margin_band
    band = self.env['nutech.margin.band'].search([('min', '<=', rec.margin)], limit=1).name
AttributeError: 'NoneType' object has no attribute 'name'
2026-10-08 09:05:10,312 51234 INFO ${db} werkzeug: 127.0.0.1 - - [08/Oct/2026 09:05:10] "POST /web/dataset/call_kw/sale.order/web_read HTTP/1.1" 200 - 18 0.012 0.061
`;

const findings = [
  { check: "H3", code: "venv-python-mismatch", severity: "error", subject: "/opt/odoo18/venv", title: "venv of /opt/odoo18 was built for Python 3.11, bin/python is now 3.13", detail: "Compiled packages (psycopg2, lxml) will fail to import.", why: "A system upgrade replaced the Python the venv links to. Odoo either refuses to start or crashes on the first compiled import.", commands: ["sudo -u odoo18 /opt/odoo18/venv/bin/python -c 'import psycopg2'"], repair: "venv", installation: "/opt/odoo18" },
  { check: "H13", code: "config-perms", severity: "warning", subject: "/etc/odoo/odoo17/acme.conf", title: "acme.conf is readable by every local user", detail: "mode 0644, contains db_password and admin_passwd", why: "Config files hold database and master passwords. The standard is owner = you, group = run-as user, mode 0640.", commands: [], repair: "config-perms", installation: "/opt/odoo17" },
  { check: "H13", code: "config-perms", severity: "warning", subject: "/etc/odoo/odoo17/demo.conf", title: "demo.conf is readable by every local user", detail: "mode 0644", why: "Config files hold database and master passwords.", commands: [], repair: "config-perms", installation: "/opt/odoo17" },
  { check: "H5", code: "addons-missing", severity: "error", subject: "/etc/odoo/odoo18/staging.conf", title: "2 addons_path entries of staging.conf do not exist", detail: "/opt/odoo18/custom/hr, /opt/odoo18/custom/payroll", why: "Odoo refuses to start when an addons_path folder is missing.", commands: [], repair: null, installation: "/opt/odoo18" },
  { check: "H8", code: "git-dubious", severity: "warning", subject: "/opt/odoo19/custom", title: "git refuses /opt/odoo19/custom: dubious ownership", detail: "owned by odoo19, you are spark", why: "git status and pull fail for you in that folder until it is marked safe.", commands: ["git config --global --add safe.directory /opt/odoo19/custom"], repair: null, installation: "/opt/odoo19" },
  { check: "H11", code: "unit-failed", severity: "error", subject: "odoo18-staging.service", title: "systemd unit odoo18-staging.service failed", detail: "Result: exit-code", why: "The service is enabled but did not start.", commands: ["journalctl -u odoo18-staging.service -n 50"], repair: null, installation: "/opt/odoo18" },
  { check: "H10", code: "orphan-config", severity: "info", subject: "/etc/odoo/odoo13/old-client.conf", title: "old-client.conf belongs to no installation", detail: "version hint 13.0", why: "Nothing can run this config; remove it or install Odoo 13.", commands: [], repair: null, installation: null },
];

const containers = [
  { app_stack: "shop18", id: "c0ffee01", name: "shop18-odoo-1", image: "odoo:18.0", version: "18.0", status: "Up 3 hours", running: true, compose: { project: "shop18", service: "odoo", working_dir: "/home/dev/odp-docker/shop18", files: ["compose.yaml"] }, ports: [{ container: "8069/tcp", host_ip: "0.0.0.0", host_port: 18069 }, { container: "8072/tcp", host_ip: "0.0.0.0", host_port: 18072 }], config: { container: "/etc/odoo", host: "/home/dev/odp-docker/shop18/config", volume: null, anonymous: false, in_image: false }, addons: [{ container: "/mnt/extra-addons", host: "/home/dev/odp-docker/shop18/addons", volume: null, anonymous: false, in_image: false }], data: { container: "/var/lib/odoo", host: null, volume: "shop18_odoo-data", anonymous: false, in_image: false }, db: { host: "db", port: "5432", user: "odoo", container: "shop18-db-1" } },
  { app_stack: null, id: "beef0002", name: "client-x-odoo", image: "registry.local/client-x:17", version: "17.0", status: "Exited (0) 2 days ago", running: false, compose: null, ports: [{ container: "8069/tcp", host_ip: null, host_port: 28069 }], config: null, addons: [], data: { container: "/var/lib/odoo", host: null, volume: "3f9c1a", anonymous: true, in_image: false }, db: { host: "10.0.0.5", port: "5432", user: "clientx", container: null } },
];
let services = [
  { name: "odoo16.service", path: "/etc/systemd/system/odoo16.service", user: "odoo16", config: "/srv/odoo16-legacy/odoo.conf", exec_start: "/srv/odoo16-legacy/venv/bin/python /srv/odoo16-legacy/server/odoo-bin -c /srv/odoo16-legacy/odoo.conf", active_state: "active", sub_state: "running", result: "success", enabled: "enabled" },
  { name: "odoo18-staging.service", path: "/etc/systemd/system/odoo18-staging.service", user: "odoo18", config: "/etc/odoo/odoo18/staging.conf", exec_start: "/opt/odoo18/venv/bin/python /opt/odoo18/odoo/odoo-bin -c /etc/odoo/odoo18/staging.conf", active_state: "failed", sub_state: "failed", result: "exit-code", enabled: "enabled" },
];

const CONFIG = (inst: any, reveal: boolean) => `[options]
addons_path = ${inst.options?.addons_path ?? ""}
admin_passwd = ${reveal ? "s3cret-master" : "********"}
db_host = localhost
db_port = 5432
db_user = ${inst.options?.db_user ?? "odoo"}
db_password = ${reveal ? "pg-pass-123" : "********"}
db_name = ${inst.options?.db_name ?? "False"}
http_port = ${inst.options?.http_port ?? "8069"}
workers = 0
log_level = info
proxy_mode = False
`;

function parseConf(text: string) {
  const o: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const m = /^\s*([a-z_]+)\s*=\s*(.*)$/.exec(line);
    if (m) o[m[1]] = m[2];
  }
  return o;
}

// ---------- Git repositories ----------
const prob = (code: string, level: string, title: string, detail = "", commands: string[] = [], heuristic = false) => ({ code, level, title, detail, commands, heuristic });
const repoState = (path: string, o: Json = {}) => ({
  path, ok: true, owner: "dev", foreign: false, branch: "main", head: "4f1c2aa9d0e3b7c1a2f4", detached: false, upstream: "origin/main",
  ahead: 0, behind: 0, staged: 0, modified: 0, untracked: 0, conflicted: 0, shallow: false, worktree: false,
  remotes: { origin: `git@git.example.com:aarsol/${path.split("/").pop()}.git` }, subject: "Merge staging fixes", committed: iso(60 * 26),
  error: null, problems: [], ...o, dirty: !!((o.staged ?? 0) + (o.modified ?? 0) + (o.conflicted ?? 0)),
});
const repo = (root: string, rel: string, purpose: string, st: Json = {}, extra: Json = {}) => {
  const path = `${root}/${rel}`;
  return { path, real: path, name: rel.split("/").pop(), gitdir: null, purpose, registered: !!extra.assoc, installations: [{ root, relative: rel, sources: ["root"], assoc: extra.assoc ?? null, alignment: null }], state: repoState(path, st) };
};
const repos: any[] = [
  repo("/opt/odoo19", "odoo", "community", { branch: "19.0", upstream: "origin/19.0", shallow: true, remotes: { origin: "https://github.com/odoo/odoo.git" }, problems: [prob("shallow", "info", "Shallow clone", "History is truncated; ahead/behind counts may be partial.")] }),
  repo("/opt/odoo19", "enterprise", "enterprise", { branch: "19.0", upstream: "origin/19.0", behind: 3 }),
  repo("/opt/odoo19", "custom/aarsol/core", "custom", { branch: "staging-19", upstream: "origin/staging-19" }, { assoc: { purpose: "custom", group: "aarsol", bulk: true } }),
  repo("/opt/odoo19", "custom/cms/admissions", "custom", { branch: "staging-19", upstream: "origin/staging-19", modified: 2, untracked: 1, problems: [prob("dirty", "warn", "Uncommitted changes", "0 staged, 2 modified. Pull and switch are blocked until you commit or stash."), prob("untracked", "info", "1 untracked file(s)", "They are never deleted by the app.")] }),
  repo("/opt/odoo19", "custom/cms/examinations", "custom", { branch: "staging-19", upstream: "origin/staging-19", behind: 5 }),
  repo("/opt/odoo19", "custom/hr/payroll", "custom", { branch: "staging-18", upstream: "origin/staging-18", problems: [prob("branch-mismatch", "info", "staging-18 may not match Odoo 19", "The branch name mentions 18. This is a guess from the name.", [], true)] }),
  repo("/opt/odoo19", "custom/hr/attendance", "custom", { branch: "main", upstream: "origin/main", ahead: 2, behind: 4, problems: [prob("diverged", "warn", "Diverged from upstream", "2 local and 4 upstream commits. Fast-forward is not possible.")] }),
  repo("/opt/odoo19", "custom/extensions/client_custom", "custom", { branch: null, detached: true, upstream: null, ahead: null, behind: null, remotes: {}, problems: [prob("detached", "warn", "Detached HEAD", "Not on a branch; pull is not possible."), prob("no-remote", "warn", "No remote", "This repository has no remote to fetch from.")] }),
  repo("/opt/odoo17", "odoo", "community", { branch: "17.0", upstream: "origin/17.0", owner: "odoo17", foreign: true, problems: [prob("not-owner", "info", "Owned by odoo17", "Shown read-only. Fetch, pull and switch run only on repositories you own.")] }),
  repo("/opt/odoo17", "custom/acme", "custom", { branch: "17.0-dev", upstream: "origin/17.0-dev", behind: 1 }),
  repo("/opt/odoo18", "enterprise", "enterprise", {}, {}),
];
repos[repos.length - 1].state = { ...repoState("/opt/odoo18/enterprise"), ok: false, worktree: true, error: ".git points to /home/odoo/.repositories/enterprise/.git/worktrees/18, which does not exist", problems: [prob("broken-worktree", "error", "Broken worktree", ".git points to a removed repository.", ["# keep the files as a plain folder:\nrm /opt/odoo18/enterprise/.git"])] };
repos[repos.length - 1].gitdir = "/home/odoo/.repositories/enterprise/.git/worktrees/18";

// ---------- Profiles ----------
const org: Json = { name: "AARSOL", install: { odoo_branch: "{series}" }, repos: [{ name: "core", url: "git@git.example.com:aarsol/core.git", branch: "staging-{version}", destination: "custom/aarsol/core", group: "aarsol" }] };
const profiles: Record<string, Json> = {
  "education-dev": { name: "Education Development", description: "Admissions, examinations and HR", odoo_version: 19, config: { workers: "2" }, "repos+": [
    { name: "admissions", url: "git@git.example.com:education/admissions.git", branch: "staging-{version}", destination: "custom/education/admissions", group: "education" },
    { name: "examinations", url: "git@git.example.com:education/examinations.git", branch: "staging-{version}", destination: "custom/education/examinations", group: "education" },
    { name: "hr", url: "git@git.example.com:education/hr.git", branch: "staging-{version}", destination: "custom/hr" }] },
  "oca-web": { name: "OCA web tools", description: "Bundle: OCA web addons", repos: [{ name: "web", url: "https://github.com/OCA/web.git", branch: "{series}", destination: "custom/oca/web" }] },
};
const fill = (x: Json, v: number): Json => typeof x === "string" ? x.replace(/\{version\}/g, String(v)).replace(/\{series\}/g, `${v}.0`) : x;
const resolveProfile = (name: string | null, version: number | null, overrides: Json) => {
  const layers: [string, Json][] = [["built-in", { install: { odoo_git: "https://github.com/odoo/odoo.git", config_name: "default" } }], ["org", org]];
  if (name) layers.push([`profile:${name}`, profiles[name]]);
  if (overrides) layers.push(["wizard", overrides]);
  const install: Json = {}; const config: Json = {}; let repos: Json[] = []; const origin: Record<string, string> = {}; let pinned: number | null = null; let title: string | null = null; let description: string | null = null;
  let rOrigin: string[] = [];
  for (const [label, d] of layers) {
    if (d.odoo_version) { pinned = d.odoo_version; origin.odoo_version = label; }
    if (d.name && label !== "wizard") title = d.name;
    if (d.description) description = d.description;
    for (const [k, x] of Object.entries(d.install ?? {})) { install[k] = x; origin[`install.${k}`] = label; }
    for (const [k, x] of Object.entries(d.config ?? {})) { config[k] = x; origin[`config.${k}`] = label; }
    if (d.repos) { repos = [...d.repos]; rOrigin = d.repos.map(() => label); }
    if (d["repos+"]) { repos = [...repos, ...d["repos+"]]; rOrigin = [...rOrigin, ...d["repos+"].map(() => label)]; }
  }
  rOrigin.forEach((l, n) => { origin[`repos.${n}`] = l; });
  const v = version ?? pinned ?? 17;
  if (pinned && version && pinned !== version && name) throw new Error(`profile:${name} is for Odoo ${pinned}, not ${version}`);
  for (const k of Object.keys(install)) install[k] = fill(install[k], v);
  const filled = repos.map((x) => Object.fromEntries(Object.entries(x).map(([k, y]) => [k, fill(y, v)])));
  return { version: v, pinned, install, config, repos: filled, origin, root: install.root || `/opt/odoo${v}`, run_as: install.run_as || `odoo${v}`, name: title, description, spec: {} };
};
const toml = (d: Json) => {
  const q = (x: Json) => typeof x === "string" ? JSON.stringify(x) : String(x);
  const out = Object.entries(d).filter(([, x]) => typeof x !== "object").map(([k, x]) => `${k} = ${q(x)}`);
  for (const t of ["install", "config"]) if (d[t]) out.push("", `[${t}]`, ...Object.entries(d[t]).map(([k, x]) => `${k} = ${q(x)}`));
  for (const t of ["repos", "repos+"]) for (const r of d[t] ?? []) out.push("", t === "repos" ? "[[repos]]" : '[["repos+"]]', ...Object.entries(r).map(([k, x]) => `${k} = ${q(x)}`));
  return out.join("\n") + "\n";
};

// ---------- Module center ----------
const modCenter = (p: Json) => {
  const db = p.database;
  const mk = (name: string, path: string, o: Json = {}) => ({ name: name.replace(/_/g, " "), version: "19.0.1.0.0", path, addons_path: path.split("/").slice(0, -1).join("/"), depends: ["base"], required_by: [], installable: true, loadable: true, own: true, problems: [], repo: path.split("/").slice(0, -1).join("/"), ...(db ? { db_state: "installed", db_version: "19.0.1.0.0", version_differs: false } : {}), ...o });
  const C = "/opt/odoo19/custom";
  const ch = (name: string, path: string, o: Json) => ({ name, path, repo: path.split("/").slice(0, -1).join("/"), files: [], kinds: [], new: false, removed: false, dependency_changed: [], ...o });
  const modules: Json = {
    base: { ...mk("base", "/opt/odoo19/odoo/odoo/addons/base", { own: false, repo: "/opt/odoo19/odoo", depends: [], required_by: ["adm_core", "exam_core", "hr_payroll_pk"] }) },
    adm_core: mk("adm_core", `${C}/cms/admissions/adm_core`, { required_by: ["adm_portal", "exam_core"], change: ch("adm_core", `${C}/cms/admissions/adm_core`, { files: [{ path: "models/applicant.py", status: "M", kind: "python" }, { path: "views/applicant_views.xml", status: "M", kind: "data" }], kinds: ["python", "data"], action: "upgrade", why: "manifest or data files (XML/CSV) changed: they load on upgrade (-u)" }) }),
    adm_portal: mk("adm_portal", `${C}/cms/admissions/adm_portal`, { depends: ["adm_core"], change: ch("adm_portal", `${C}/cms/admissions/adm_portal`, { dependency_changed: ["adm_core"], action: "review", why: "depends on changed adm_core: upgrade only if it relies on what changed" }) }),
    exam_core: mk("exam_core", `${C}/cms/examinations/exam_core`, { depends: ["adm_core"], ...(db ? { db_version: "19.0.0.9.0", version_differs: true } : {}) }),
    exam_results: mk("exam_results", `${C}/cms/examinations/exam_results`, { depends: ["exam_core"], change: ch("exam_results", `${C}/cms/examinations/exam_results`, { files: [{ path: "__manifest__.py", status: "??", kind: "manifest" }, { path: "models/result.py", status: "??", kind: "python" }], kinds: ["manifest", "python"], new: true, action: "install", why: "a new module: install it (-i) where it is needed" }), ...(db ? { db_state: null } : {}) }),
    hr_payroll_pk: mk("hr_payroll_pk", `${C}/hr/payroll/hr_payroll_pk`, { version: "18.0.1.2.0", problems: [{ code: "series-version", level: "warn", text: "version 18.0.1.2.0 does not start with the installation's series 19.0" }, { code: "missing-files", level: "error", text: "data lists files that do not exist: views/old_report.xml" }] }),
    client_custom: mk("client_custom", `${C}/extensions/client_custom/client_custom`, { loadable: false, problems: [{ code: "not-loaded", level: "info", text: `${C}/extensions/client_custom is not on this config's addons_path` }], ...(db ? { db_state: undefined } : {}) }),
  };
  return { modules, missing: {}, cycles: [], addons_paths: [], installation: "/opt/odoo19", series: "19.0", config: p.config, instance: "nutech", repos: [], database: db ?? null, db_error: null,
    changed: { modules: { adm_core: modules.adm_core.change, adm_portal: modules.adm_portal.change, exam_results: modules.exam_results.change,
      old_reports: ch("old_reports", `${C}/hr/payroll/old_reports`, { files: [{ path: "__manifest__.py", status: "D", kind: "manifest" }], kinds: ["manifest"], removed: true, action: "uninstall-first", why: "the module folder lost its manifest: uninstall it from databases before removing it, or restore it" }) }, errors: {}, heuristic: true } };
};
const testRuns: Json[] = [
  { id: "t2", at: iso(40), installation: "/opt/odoo19", config: "/etc/odoo/odoo19/nutech.conf", modules: ["exam_core"], tags: null, demo: true, database: "odp_test_exam_core_20261009_101500", kept: true, exit_code: 0, status: "failed", tests: 24, failures: 1, errors: 0, failed: [{ kind: "fail", test: "odoo.addons.exam_core.tests.test_marks.TestMarks.test_rounding" }], note: null },
  { id: "t1", at: iso(180), installation: "/opt/odoo19", config: "/etc/odoo/odoo19/nutech.conf", modules: ["adm_core"], tags: null, demo: true, database: "odp_test_adm_core_20261009_080000", kept: false, exit_code: 0, status: "passed", tests: 31, failures: 0, errors: 0, failed: [], note: null },
];

// ---------- Python environment ----------
const pyEnv = (root: string) => {
  const r = (file: string, name: string, spec: string, installed: string | null, status: string, detail = "", installed_as: string | null = null) =>
    ({ file: `${root}/${file}`, name, raw: `${name}${spec}`, spec, marker: null, installed, installed_as: installed_as ?? (installed ? name : null), status, detail });
  const requirements = [
    r("odoo/requirements.txt", "lxml", "==5.2.1", "5.2.1", "ok"), r("odoo/requirements.txt", "babel", "==2.10.3", "2.10.3", "ok"),
    r("odoo/requirements.txt", "psycopg2", "==2.9.9", "2.9.9", "ok", "", "psycopg2-binary"), r("odoo/requirements.txt", "werkzeug", "==3.0.1", "3.0.1", "ok"),
    r("odoo/requirements.txt", "gevent", "==22.10.2", null, "not-applicable", "marker excludes Python 3.12"),
    r("openupgrade/requirements.txt", "openupgradelib", "", null, "missing", "not installed in the venv"),
    r("partners/nims/requirements.txt", "pandas", "==2.2.3", "3.0.6", "mismatch", "3.0.6 installed, ==2.2.3 asked"),
    r("custom/hr/payroll/requirements.txt", "lxml", "==4.9.0", "5.2.1", "mismatch", "5.2.1 installed, ==4.9.0 asked"),
  ];
  return { root, version: "19.0", run_as: "odoo19",
    interpreter: { venv: `${root}/venv`, python: `${root}/venv/bin/python`, target: "/opt/odoo-dev-panel/python/cpython-3.12.14-linux-x86_64-gnu/bin/python3.12", version: "3.12", built_for: "3.12", pinned: "3.12", uv_managed: true, system_python: false, problem: root === "/opt/odoo18" ? "bin/python is now Python 3.13, but the venv was built with Python 3.11" : null, matches_pin: true },
    files: [`${root}/odoo/requirements.txt`, `${root}/openupgrade/requirements.txt`, `${root}/partners/nims/requirements.txt`, `${root}/custom/hr/payroll/requirements.txt`],
    requirements, counts: { ok: 4, missing: 1, mismatch: 2, "not-applicable": 1, unknown: 0 },
    conflicts: [{ name: "lxml", reason: "pinned to different versions: 4.9.0, 5.2.1", asked: [{ file: `${root}/odoo/requirements.txt`, spec: "==5.2.1" }, { file: `${root}/custom/hr/payroll/requirements.txt`, spec: "==4.9.0" }] }],
    packages: { babel: "2.10.3", lxml: "5.2.1", pandas: "3.0.6", "psycopg2-binary": "2.9.9", werkzeug: "3.0.1", "ipython": "8.30.0", "black": "24.10.0" }, extras: ["black", "ipython"] };
};

const plan = (checks: [string, string, string][], steps: [string, string, string[]][]) => ({
  checks: checks.map(([id, status, detail]) => ({ id, status, detail })),
  steps: steps.map(([id, title, commands], i) => ({ id, phase: i + 1, actor: i ? "agent" : "root", title, commands })),
  ok: checks.every(([, s]) => s !== "fail"),
});

export function createMock(deliver: Deliver) {
  const reply = (id: Json, result: Json, delay = 80 + Math.random() * 180) => setTimeout(() => deliver({ jsonrpc: "2.0", id, result }), delay);
  const fail = (id: Json, message: string) => setTimeout(() => deliver({ jsonrpc: "2.0", id, error: { code: -32000, message } }), 120);
  const emit = (method: string, params: Json) => deliver({ jsonrpc: "2.0", method, params });
  const pending = new Map<string, (r: Json) => void>();
  let reqId = 0;
  const askUi = (method: string, params: Json) => new Promise<Json>((resolve) => {
    const id = `mock-${++reqId}`;
    pending.set(id, resolve);
    deliver({ jsonrpc: "2.0", id, method, params });
  });
  const askPassword = async (user: string, purpose: string) => {
    const r = await askUi("ui.askPassword", { prompt: `[sudo: authenticate] Password:`, user, purpose });
    return !!r?.password;
  };
  let runCounter = 0;
  const job = (prefix: string, steps: string[], extra: Json = {}, okResult = true) => {
    const run_id = `run-${++runCounter}`;
    let t = 300;
    for (const s of steps) {
      setTimeout(() => emit(`${prefix}.step`, { run_id, step: s, status: "start", text: s.replace(/-/g, " ") }), t);
      t += 250;
      setTimeout(() => emit(`${prefix}.step`, { run_id, step: s, status: "output", text: `  … ${s} output line` }), t);
      t += 250;
      setTimeout(() => emit(`${prefix}.step`, { run_id, step: s, status: "ok", text: "" }), t);
      t += 150;
    }
    setTimeout(() => emit(`${prefix}.finished`, { run_id, ok: okResult, error: okResult ? null : "mock failure", ...extra }), t + 200);
    return { run_id };
  };

  // Live output for followed sessions.
  const followers = new Map<string, { offset: number; timer: ReturnType<typeof setInterval> | null }>();
  const follow = (user: string, id: string) => {
    const s = sessions.find((x) => x.user === user && x.id === id);
    if (!s) return;
    const key = `${user}/${id}`;
    followers.get(key)?.timer && clearInterval(followers.get(key)!.timer!);
    const text = s.pty ? ">>> env['res.partner'].search_count([])\n1284\n>>> " : LOG(s.meta?.db ?? "?").repeat(s.state === "running" ? 30 : 2);
    let offset = text.length;
    setTimeout(() => emit("session.output", { user, id, offset, data: text }), 60);
    const timer = s.state === "running" && !s.pty ? setInterval(() => {
      const t = new Date();
      const stamp = t.toISOString().replace("T", " ").slice(0, 19) + "," + String(t.getMilliseconds()).padStart(3, "0");
      const line = `${stamp} ${s.pid} INFO ${s.meta?.db ?? "?"} werkzeug: 127.0.0.1 - - "GET /web/webclient/load_menus HTTP/1.1" 200 - 3 0.002 0.011\n`;
      offset += line.length;
      emit("session.output", { user, id, offset, data: line });
    }, 1600) : null;
    followers.set(key, { offset, timer });
  };

  const snapshotOf = () => ({
    installations, instances,
    databases: installations.map((i) => ({ installation: i.root, databases: i.root === "/opt/odoo18" ? [] : dbs[i.root] ?? [], error: i.root === "/opt/odoo18" ? "agent odoo18 is locked: unlock it to list databases" : null })),
    processes: [{ pid: 2881, user: "odoo16", port: 8069, instance: "/srv/odoo16-legacy/odoo.conf", installation: "/srv/odoo16-legacy", database: null, unit: "odoo16.service", argv: [] }],
    units: services.map((s) => ({ name: s.name, path: s.path, user: s.user, config: s.config, exec_start: s.exec_start, active_state: s.active_state, sub_state: s.sub_state, result: s.result })),
    ports: { listening: [22, 5432, 8069, 8072], odoo: [], conflicts: [] },
    missing: [], unreadable: ["/opt/containerd"],
  });



  const provisionPlan = (p: Json) => {
    const r = resolveProfile(p.profile, p.version, p.overrides);
    const v = r.version;
    const root = r.root, run_as = r.run_as;
    const ent = r.install.enterprise_git ? "git" : r.install.enterprise_archive ? "archive" : null;
    const tree = [{ path: "odoo", kind: "community", label: r.install.odoo_branch || `${v}.0`, addons: true }, { path: "venv", kind: "venv", label: "Python 3.12", addons: false },
      ...(ent ? [{ path: "enterprise", kind: "enterprise", label: ent === "archive" ? "archive" : r.install.enterprise_branch || `${v}.0`, addons: true }] : []),
      ...r.repos.map((x: Json) => ({ path: x.destination || `custom/${x.name}`, kind: x.purpose || "custom", label: x.branch || "default branch", addons: x.addons !== false, name: x.name, group: x.group ?? null }))];
    const exists = installations.some((i) => i.root === root);
    const preflight = [
      { id: "root-path", status: exists ? "warn" : "ok", detail: exists ? `${root} already exists: it is reused, existing files are not overwritten` : `${root} does not exist` },
      { id: "postgres", status: "ok", detail: "PostgreSQL answers on localhost:5432" }, { id: "disk", status: "ok", detail: "48.2 GiB free" },
      ...tree.filter((t) => t.kind !== "venv").map((t) => ({ id: t.kind === "community" || t.kind === "enterprise" ? t.kind : `repo:${(t as Json).name}`, status: "ok", detail: `${root}/${t.path} will be created` })),
      { id: "remote:odoo", status: "ok", detail: `https://github.com/odoo/odoo.git: ${v}.0 found` },
      ...r.repos.map((x: Json) => ({ id: `remote:${x.name}`, status: /examinations/.test(x.url) ? "fail" : "ok", detail: /examinations/.test(x.url) ? `${x.url}: Authentication failed` : `${x.url}: ${x.branch || "default branch"} found` })),
      { id: "wkhtmltopdf", status: "warn", detail: "wkhtmltopdf not found. Odoo needs the patched-Qt build from wkhtmltopdf.org" },
    ];
    const addons = [`${root}/odoo/addons`, ...(ent ? [`${root}/enterprise`] : []), ...tree.filter((t) => t.addons && t.kind !== "community" && t.kind !== "enterprise").map((t) => `${root}/${t.path}`)];
    return {
      spec: { version: v, run_as, root, python: "3.12", odoo_git: r.install.odoo_git, odoo_branch: r.install.odoo_branch || `${v}.0`, enterprise_git: r.install.enterprise_git || null, enterprise_branch: r.install.enterprise_branch || null, enterprise_archive: r.install.enterprise_archive || null, config_name: r.install.config_name || "default", conf_path: `/etc/odoo/${run_as}/${r.install.config_name || "default"}.conf` },
      preflight, ok: !preflight.some((c) => c.status === "fail"),
      steps: [{ id: "root-script", phase: 1, actor: "root", title: "Create user, directories, packages, PostgreSQL role and agent (one sudo prompt)", commands: ["sudo -A bash <generated root script>"] },
        { id: "clone-odoo", phase: 2, actor: "dev", title: `Clone Odoo community ${v}.0`, commands: [`git clone --depth=1 --single-branch --no-tags --branch ${v}.0 https://github.com/odoo/odoo.git ${root}/odoo`] },
        ...r.repos.map((x: Json) => ({ id: `custom-${x.name}`, phase: 2, actor: "dev", title: `Clone ${x.name} into ${x.destination || `custom/${x.name}`}`, commands: [`git clone --depth=1 --single-branch --no-tags ${x.branch ? `--branch ${x.branch} ` : ""}${x.url} ${root}/${x.destination || `custom/${x.name}`}`] })),
        { id: "venv", phase: 3, actor: "agent", title: "Create the virtual environment", commands: [] }, { id: "config", phase: 4, actor: "dev", title: "Write the config", commands: [] }],
      root_script: `#!/bin/bash\nset -euo pipefail\nid ${run_as} || useradd --system --home ${root} ${run_as}\ninstall -d -m 2775 ${root}`,
      config: `[options]\nadmin_passwd = <generated at run time>\ndb_user = ${run_as}\naddons_path = ${addons.join(",\n\t")}\n${Object.entries(r.config).map(([k, x]) => `${k} = ${x}`).join("\n")}`,
      addons_path: addons, tree, remote_checked: true,
      profile: { name: r.name, description: r.description, origin: r.origin, pinned: r.pinned, version: v },
      previous: root === "/opt/odoo18" ? { path: "/opt/odoo18/.odp-provision.json", status: "incomplete", last_phase: "failed in python", updated_at: "2026-10-08T14:02:11+00:00", completed: ["root-script", "clone"], remaining: ["pip", "verify"] } : null,
    };
  };
  const repoPlan = (p: Json) => {
    const pick: string[] = p.op === "clone" || p.op === "bundle" ? [] : p.bulk ? repos.filter((r) => !p.installation || r.installations.some((i: Json) => i.root === p.installation)).map((r) => r.path) : p.repos ?? [p.repo];
    const items = pick.map((path: string) => {
      const r = repos.find((x) => x.path === path)!;
      const s = r.state;
      let skip: string | null = null;
      if (!s.ok) skip = s.problems[0]?.title ?? "unreadable";
      else if (s.foreign) skip = `owned by ${s.owner}; write operations run only on repositories you own`;
      else if (p.op === "pull" && s.dirty) skip = "has uncommitted changes";
      else if (p.op === "pull" && s.detached) skip = "detached HEAD (not on a branch)";
      else if ((p.op === "pull" || p.op === "fetch") && !Object.keys(s.remotes).length) skip = "has no remote";
      else if (p.op === "pull" && s.ahead && s.behind) skip = `diverged from ${s.upstream} (${s.ahead} ahead, ${s.behind} behind)`;
      else if (p.op === "switch" && s.dirty) skip = "has uncommitted changes";
      const cmd = { fetch: `git -C ${path} fetch --prune origin`, pull: `git -C ${path} pull --ff-only --no-rebase`, switch: `git -C ${path} switch --track origin/${p.branch}`, checkout: `git -C ${path} switch --detach ${p.ref}` }[p.op as string];
      return { repo: path, title: `${p.op} ${r.name}`, commands: skip ? [] : [cmd], skip, problems: s.problems, level: skip ? (p.op === "switch" ? "fail" : "warn") : "ok" };
    });
    if (p.op === "bundle") {
      const r = resolveProfile(p.profile, 19, null);
      const items = r.repos.map((x: Json) => {
        const target = `${p.installation}/${x.destination || `custom/${x.name}`}`;
        const there = repos.some((y) => y.path === target);
        return { repo: target, title: `${there ? "Keep" : "Clone"} ${x.name} into ${x.destination}`, commands: there ? [] : [`git clone --depth=1 --single-branch --no-tags ${x.branch ? `--branch ${x.branch} ` : ""}-- ${x.url} ${target}`],
          skip: there ? "already there: kept, not fetched or switched" : null, problems: [], level: "ok" };
      });
      const run = items.filter((i: Json) => !i.skip);
      return { op: "bundle", ok: true, items, checks: [], steps: run.map((i: Json, n: number) => ({ id: `bundle-${n + 1}`, phase: n + 1, actor: "dev", title: i.title, commands: i.commands })), counts: { run: run.length, skip: items.length - run.length }, register: null };
    }
    if (p.op === "clone") {
      const target = `${p.installation}/${p.destination}`;
      const bad = /:[^@/]*@/.test(p.url ?? "") ? "the URL contains a password; use an SSH key or a Git credential helper instead" : null;
      const exists = repos.some((r) => r.path === target);
      const checks = [
        { id: "url", status: bad ? "fail" : "ok", detail: bad ?? p.url },
        { id: "destination", status: exists ? "fail" : "ok", detail: exists ? `${target} is already a repository; add it as an existing repository` : target },
        { id: "writable", status: "ok", detail: `${p.installation}/custom is writable` },
        { id: "git", status: "ok", detail: "git is installed" },
      ];
      const ok = checks.every((c) => c.status !== "fail");
      const cmd = `git clone ${p.shallow === false ? "" : "--depth=1 --single-branch --no-tags "}${p.ref ? `--branch ${p.ref} ` : ""}-- ${p.url} ${target}`;
      return { op: "clone", ok, checks, items: [{ repo: target, title: `Clone into ${p.destination}`, commands: ok ? [cmd] : [], skip: ok ? null : "checks failed", problems: [], level: ok ? "ok" : "fail" }], steps: ok ? [{ id: "clone-1", phase: 1, actor: "dev", title: `Clone into ${p.destination}`, commands: [cmd] }] : [], counts: { run: ok ? 1 : 0, skip: ok ? 0 : 1 }, register: null };
    }
    const checks: Json[] = items.filter((i: Json) => i.skip).map((i: Json) => ({ id: i.repo, status: i.level, detail: `${i.repo}: ${i.skip}` }));
    if (p.op === "checkout") checks.push({ id: "confirm", status: p.confirm === p.ref ? "ok" : "fail", detail: p.confirm === p.ref ? "confirmed" : `type ${p.ref} to confirm a detached checkout` });
    const runnable = items.filter((i: Json) => !i.skip);
    return { op: p.op, ok: runnable.length > 0 && !checks.some((c) => c.status === "fail" && c.id === "confirm"), items, checks,
      steps: runnable.map((i: Json, n: number) => ({ id: `${p.op}-${n + 1}`, phase: n + 1, actor: "dev", title: i.title, commands: i.commands })),
      counts: { run: runnable.length, skip: items.length - runnable.length }, register: null };
  };
  // ---- K1-K3 task runner
  const SAFE = `name = "Safe upgrade"\ndescription = "Snapshot the database and filestore, upgrade the modules, then run their tests in a throwaway database"\n\n[params.config]\nkind = "config"\ndescription = "Odoo config of the instance"\n\n[params.database]\nkind = "database"\ndescription = "Database to upgrade"\n\n[params.modules]\nkind = "modules"\ndescription = "Modules to upgrade"\n\n[[steps]]\nop = "db.snapshot"\ntitle = "Snapshot {database}"\nconfig = "{config}"\ndatabase = "{database}"\n\n[[steps]]\nop = "modules.upgrade"\ntitle = "Upgrade {modules} in {database}"\nconfig = "{config}"\ndatabase = "{database}"\nmodules = "{modules}"\nsnapshot = false\n\n[[steps]]\nop = "modules.test"\ntitle = "Test {modules}"\nconfig = "{config}"\nmodules = "{modules}"\n`;
  const wfs: Record<string, Json> = {
    "safe-upgrade": { source: "built-in", title: "Safe upgrade", description: "Snapshot the database and filestore, upgrade the modules, then run their tests in a throwaway database", text: SAFE,
      params: { config: { kind: "config", description: "Odoo config of the instance" }, database: { kind: "database", description: "Database to upgrade" }, modules: { kind: "modules", description: "Modules to upgrade" } },
      steps: [{ op: "db.snapshot", title: "Snapshot {database}", gate: false }, { op: "modules.upgrade", title: "Upgrade {modules} in {database}", gate: true }, { op: "modules.test", title: "Test {modules}", gate: false }] },
    "pull-and-upgrade": { source: "built-in", title: "Pull and upgrade", description: "Pull the installation's repositories (fast-forward only), snapshot, upgrade the modules, start the instance", text: "name = \"Pull and upgrade\"\n# …\n",
      params: { config: { kind: "config" }, database: { kind: "database" }, modules: { kind: "modules" } },
      steps: [{ op: "instance.stop", title: "Stop an instance's sessions", gate: false }, { op: "git.pull", title: "Pull repositories (fast-forward only)", gate: false }, { op: "db.snapshot", title: "Snapshot a database", gate: false }, { op: "modules.upgrade", title: "Upgrade modules", gate: true }, { op: "instance.start", title: "Start an instance", gate: false }] },
    "test-copy": { source: "built-in", title: "Neutralized test copy", description: "Clone a database with its filestore, neutralize the copy, start the instance on it", text: "name = \"Neutralized test copy\"\n# …\n",
      params: { config: { kind: "config" }, database: { kind: "database", description: "Database to copy (it is only read)" }, target: { kind: "new_database", description: "Name of the copy" } },
      steps: [{ op: "db.clone", title: "Clone a database", gate: false }, { op: "db.neutralize", title: "Neutralize a database", gate: false }, { op: "instance.start", title: "Start an instance", gate: false }] },
    "nightly-check": { source: "saved", title: "Nightly check", description: "Fetch, validate the venv, run a lint command as me", text: "name = \"Nightly check\"\n\n[params.config]\nkind = \"config\"\n\n[[steps]]\nop = \"git.fetch\"\ninstallation = \"{installation}\"\n\n[[steps]]\nop = \"python.validate\"\nconfig = \"{config}\"\n\n[[steps]]\nop = \"command\"\nargv = [\"pre-commit\", \"run\", \"--all-files\"]\ncwd = \"/opt/odoo19/custom\"\n",
      params: { config: { kind: "config" } },
      steps: [{ op: "git.fetch", title: "Fetch repositories", gate: false }, { op: "python.validate", title: "Validate the Python environment", gate: false }, { op: "command", title: "Run a command", gate: true }] },
  };
  const taskRuns: Json[] = [
    { run: "5be1c0de", workflow: "safe-upgrade", title: "Safe upgrade", at: iso(180), ended_at: iso(176), status: "fail", failed_at: 1, start: 0, retry_of: null, user: "dev",
      params: { config: "/etc/odoo/odoo19/nutech.conf", database: "nutech_upgrade_test", modules: ["nims_hr", "nims_payroll"], installation: "/opt/odoo19" }, titles: [],
      steps: [{ index: 0, op: "db.snapshot", title: "Snapshot nutech_upgrade_test", status: "ok", summary: "/opt/odoo19/odp-backups/snapshots/nutech_upgrade_test-20261009-0912", seconds: 41.2 },
        { index: 1, op: "modules.upgrade", title: "Upgrade nims_hr,nims_payroll in nutech_upgrade_test", status: "fail", summary: "exit code 1", seconds: 63.9 }] },
    { run: "1d9a77f2", workflow: "test-copy", title: "Neutralized test copy", at: iso(60 * 26), ended_at: iso(60 * 26 - 3), status: "ok", failed_at: null, start: 0, retry_of: null, user: "dev",
      params: { config: "/etc/odoo/odoo17/acme.conf", database: "acme_prod", target: "acme_test_2026_10" }, titles: [],
      steps: [{ index: 0, op: "db.clone", title: "Clone a database", status: "ok", summary: "acme_test_2026_10", seconds: 102.4 }, { index: 1, op: "db.neutralize", title: "Neutralize a database", status: "ok", summary: "neutralize", seconds: 3.1 },
        { index: 2, op: "instance.start", title: "Start an instance", status: "ok", summary: "running on http://localhost:8069", seconds: 2.0 }] },
  ];
  const taskGates = new Map<string, (ok: boolean) => void>();
  const fillT = (t: string, v: Json) => t.replace(/\{(\w+)\}/g, (_m, k) => (Array.isArray(v[k]) ? v[k].join(",") : v[k] ?? `{${k}}`));
  const taskValues = (w: Json, given: Json) => {
    const v: Json = {};
    for (const [k, spec] of Object.entries<Json>(w.params)) {
      const raw = given?.[k];
      if (!raw || (Array.isArray(raw) && !raw.length)) throw new Error(`parameter ${k} is required`);
      v[k] = spec.kind === "modules" ? String(raw).split(",").map((x) => x.trim()).filter(Boolean) : raw;
    }
    const cfg = Object.keys(w.params).find((k) => w.params[k].kind === "config");
    if (cfg) v.installation = instances.find((i) => i.path === v[cfg])?.installation ?? "/opt/odoo19";
    return v;
  };
  const taskStep = (w: Json, n: number, v: Json) => {
    const st = w.steps[n];
    const identity = st.op.startsWith("db.") ? `${v.installation?.split("/").pop()} (PostgreSQL role and filestore owner)` : st.op === "command" ? "dev (you, with your own permissions)"
      : st.op.startsWith("git.") ? "dev (you)" : `${v.installation?.split("/").pop()} (run-as user, through its agent)`;
    const commands = st.op === "db.snapshot" ? [`pg_dump -Fc -f /opt/odoo19/odp-backups/snapshots/${v.database}-20261009-1200/dump ${v.database}`]
      : st.op === "modules.upgrade" ? [`/opt/odoo19/venv/bin/python /opt/odoo19/odoo/odoo-bin -c ${v.config} -d ${v.database} -u ${(v.modules ?? []).join(",")} --stop-after-init`]
      : st.op === "modules.test" ? [`/opt/odoo19/venv/bin/python /opt/odoo19/odoo/odoo-bin -c ${v.config} -d odp_test_x -i ${(v.modules ?? []).join(",")} --test-enable --stop-after-init`]
      : st.op === "command" ? ["pre-commit run --all-files"] : st.op === "git.pull" || st.op === "git.fetch" ? [`git -C /opt/odoo19/custom/nims ${st.op.slice(4)} --ff-only`] : [`odp ${st.op} …`];
    const later = st.op === "db.neutralize" && !dbs[v.installation]?.some((d) => d.name === v.target);
    const checks = later ? [{ id: "source", status: "fail", detail: `${v.target} is not a database of this installation's role` }] : st.op === "command" ? [{ id: "identity", status: "warn", detail: `runs as ${identity}, in /opt/odoo19/custom` }] : [{ id: "plan", status: "ok", detail: "checks pass" }];
    return { index: n, op: st.op, title: fillT(st.title, v), gate: st.gate, ok: !later, checks, commands, identity };
  };
  let taskCurrent: { run: string; cancel: boolean } | null = null;
  const handlers: Record<string, (p: Json) => Json | Promise<Json>> = {
    "tasks.list": () => Object.entries(wfs).map(([name, w]) => ({ name, source: w.source, path: w.source === "saved" ? `/home/dev/.config/odoo-dev-panel/workflows/${name}.toml` : null, title: w.title, description: w.description, steps: w.steps.length, params: Object.keys(w.params), error: null }))
      .sort((a, b) => (a.source === b.source ? a.name.localeCompare(b.name) : a.source === "saved" ? -1 : 1)),
    "tasks.read": (p) => {
      const w = wfs[p.name];
      if (!w) throw new Error(`no workflow ${p.name}`);
      return { name: p.name, source: w.source, path: w.source === "saved" ? `/home/dev/.config/odoo-dev-panel/workflows/${p.name}.toml` : null, text: w.text, digest: "abc", title: w.title, description: w.description, params: w.params, derived: Object.values<Json>(w.params).some((x) => x.kind === "config") ? ["installation"] : [] };
    },
    "tasks.ops": () => ({ param_kinds: ["installation", "config", "database", "new_database", "modules", "text"], ops: [
      { op: "git.pull", title: "Pull repositories (fast-forward only)", fields: { installation: "text", repos: "list" }, required: [], gate: false, always_gate: false },
      { op: "db.snapshot", title: "Snapshot a database", fields: { installation: "text", config: "text", database: "text", keep: "int" }, required: ["database"], gate: false, always_gate: false },
      { op: "modules.upgrade", title: "Upgrade modules", fields: { config: "text", database: "text", modules: "list", snapshot: "bool" }, required: ["config", "database", "modules"], gate: true, always_gate: false },
      { op: "db.drop", title: "Drop a database", fields: { installation: "text", config: "text", database: "text" }, required: ["database"], gate: true, always_gate: true },
      { op: "command", title: "Run a command", fields: { argv: "list", as: "text", cwd: "text", installation: "text", config: "text" }, required: ["argv"], gate: true, always_gate: true }] }),
    "tasks.check": (p) => (/\[\[steps\]\]/.test(p.text) ? { ok: true, error: null } : { ok: false, error: "workflow: steps must be a non-empty array of tables ([[steps]])" }),
    "tasks.save": (p) => {
      if (wfs[p.name]?.source === "built-in") throw new Error(`${p.name} is a built-in recipe; save the copy under another name`);
      if (wfs[p.name] && !p.overwrite) throw new Error(`workflow ${p.name} exists; choose another name or overwrite it`);
      const title = /name = "([^"]*)"/.exec(p.text)?.[1] ?? p.name;
      wfs[p.name] = { ...(wfs[p.name] ?? { params: {}, steps: [{ op: "db.snapshot", title: "Snapshot a database", gate: false }] }), source: "saved", title, description: /description = "([^"]*)"/.exec(p.text)?.[1] ?? null, text: p.text };
      return { path: `/home/dev/.config/odoo-dev-panel/workflows/${p.name}.toml` };
    },
    "tasks.delete": (p) => { delete wfs[p.name]; return { moved_to: `/home/dev/.config/odoo-dev-panel/workflows/.trash-${p.name}-20261009-120000.toml` }; },
    "tasks.preview": (p) => {
      const prev = p.retry_of ? taskRuns.find((r) => r.run === p.retry_of) : null;
      const name = prev ? prev.workflow : p.name;
      const w = wfs[name];
      const v = prev ? prev.params : taskValues(w, p.params);
      const start = prev ? prev.failed_at ?? 0 : 0;
      const steps = w.steps.map((_s: Json, n: number) => (n < start ? { ...taskStep(w, n, v), gate: false, checks: [], commands: [], skipped: "done in the earlier run" } : taskStep(w, n, v)));
      return { name, title: w.title, description: w.description, source: w.source, digest: "abc", params: v, start, steps, gates: steps.filter((s: Json) => s.gate && !s.skipped).length };
    },
    "tasks.run": async (p) => {
      const prev = p.retry_of ? taskRuns.find((r) => r.run === p.retry_of) : null;
      const name = prev ? prev.workflow : p.name;
      const w = wfs[name];
      const v = prev ? prev.params : taskValues(w, p.params);
      const start = prev ? prev.failed_at ?? 0 : 0;
      const run_id = `t${++runCounter}`;
      const row: Json = { run: run_id, workflow: name, title: w.title, at: new Date().toISOString(), ended_at: null, status: "running", failed_at: null, start, params: v, retry_of: p.retry_of ?? null, titles: [], steps: [], user: "dev" };
      taskRuns.unshift(row);
      taskCurrent = { run: run_id, cancel: false };
      const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
      (async () => {
        await sleep(200);
        let status = "ok", failed: number | null = null;
        for (let n = start; n < w.steps.length; n++) {
          if (taskCurrent?.cancel) { status = "cancelled"; failed = n; break; }
          const planned = taskStep(w, n, v);
          emit("tasks.step", { run_id, task_step: n, step: "task", status: "start", text: planned.title });
          await sleep(300);
          let st = "ok", summary = "";
          if (planned.gate) {
            emit("tasks.step", { run_id, task_step: n, step: "task", status: "gate", text: planned.title, plan: { identity: planned.identity, commands: planned.commands, checks: planned.checks, ok: true } });
            const ok = await new Promise<boolean>((r) => taskGates.set(`${run_id}/${n}`, r));
            if (!ok) { st = "declined"; summary = "not confirmed"; }
          }
          if (st === "ok") {
            for (const line of [`${planned.commands[0]}`, "… working", "… done"]) { emit("tasks.step", { run_id, task_step: n, step: planned.op.split(".").pop(), status: "output", text: line }); await sleep(250); }
            const fail = planned.op === "modules.test" && (v.modules ?? []).includes("broken");
            st = fail ? "fail" : "ok";
            summary = fail ? "failed: 12 tests, 2 failed, 0 errors; odp_test_broken kept" : planned.op === "db.snapshot" ? `/opt/odoo19/odp-backups/snapshots/${v.database}-20261009-1200` : planned.op.startsWith("modules.") ? (planned.op === "modules.test" ? "passed: 48 tests, 0 failed, 0 errors" : "exit code 0") : "done";
          }
          emit("tasks.step", { run_id, task_step: n, step: "task", status: st, text: summary });
          row.steps.push({ index: n, op: planned.op, title: planned.title, status: st, summary, seconds: 1.1 });
          if (st !== "ok") { status = st; failed = n; break; }
        }
        Object.assign(row, { status, failed_at: failed, ended_at: new Date().toISOString() });
        emit("tasks.finished", { run_id, ok: true, error: null, workflow: name, status, failed_at: failed, steps: row.steps, retry: failed != null });
      })();
      return { run_id, workflow: name, start };
    },
    "tasks.confirm": (p) => {
      const key = `${p.run_id}/${p.step}`;
      const r = taskGates.get(key);
      if (!r) throw new Error("no step is waiting for that confirmation");
      taskGates.delete(key); r(p.approve === true);
      return { approved: p.approve === true };
    },
    "tasks.cancel": (p) => {
      if (taskCurrent?.run !== p.run_id) throw new Error("no such task run");
      taskCurrent!.cancel = true;
      for (const [k, r] of taskGates) if (k.startsWith(`${p.run_id}/`)) { taskGates.delete(k); r(false); }
      return { cancelling: true };
    },
    "tasks.history": (p) => taskRuns.filter((r) => !p?.workflow || r.workflow === p.workflow),
    "app.info": () => ({ version: "0.6.0-dev", user: "dev", socket_dir: "/run/odoo-dev-panel", pid: 1, group: { group: "odoo-dev", exists: true, member: true, active: true } }),
    "agents.list": () => agents,
    "sessions.list": () => [...sessions].sort((a, b) => b.started_at.localeCompare(a.started_at)),
    "discover.scan": () => snapshotOf(),
    "python.env": (p) => pyEnv(p.root),
    "python.tools": () => ({ tools: [
      { tool: "debugpy", scope: "venv", installed: false, version: null, detail: "in the installation's venv: lets an IDE attach to Odoo" },
      { tool: "rtlcss", scope: "system", installed: true, version: "rtlcss version: 4.3.0", detail: "right-to-left CSS" },
      { tool: "wkhtmltopdf", scope: "system", installed: true, version: "wkhtmltopdf 0.12.6 (Ubuntu package)", patched: false, detail: "PDF reports; this build is not the patched-Qt one Odoo needs" }] }),
    "python.disk": (p) => ({ root: p.root, parts: [{ label: "venv", path: `${p.root}/venv`, bytes: 612_000_000, complete: true }, { label: "odoo source", path: `${p.root}/odoo`, bytes: 1_420_000_000, complete: true }, { label: "custom", path: `${p.root}/custom`, bytes: 380_000_000, complete: true }], free: 48_200_000_000, total: 250_000_000_000 }),
    "python.export": () => ({ text: "# Odoo 19.0, Python 3.12\n# 7 packages, exported by Odoo Dev Panel\nbabel==2.10.3\nlxml==5.2.1\n" }),
    "python.plan": (p) => {
      const pk = p.op === "install" ? [...(p.packages ?? []), ...(p.missing ? ["openupgradelib", "pandas==2.2.3", "lxml==4.9.0"] : [])] : p.op === "tool" ? [p.tool] : ["lxml", "babel", "psycopg2-binary", "werkzeug"];
      const system = p.op === "tool" && p.tool !== "debugpy";
      const checks = [{ id: "venv", status: "ok", detail: `${p.root}/venv/bin/python (Python 3.12)` }, { id: "agent", status: "ok", detail: "agent of odoo19 is running" },
        ...(p.op === "install" ? [{ id: "running", status: "warn", detail: "Odoo runs from this venv (pid 2881): restart it afterwards to load the change" }] : [])];
      const script = p.tool === "wkhtmltopdf" ? "#!/usr/bin/env bash\nset -euo pipefail\nDEB=/home/dev/.local/state/odoo-dev-panel/downloads/wkhtmltox_0.12.6.1-3.jammy_amd64.deb\necho \"4f723b26...  $DEB\" | sha256sum -c -\napt-get install -y -qq \"$DEB\"\nwkhtmltopdf --version\n" : p.tool === "rtlcss" ? "#!/usr/bin/env bash\nset -euo pipefail\napt-get install -y -qq nodejs npm\nnpm install -g rtlcss\n" : undefined;
      return { kind: p.op === "tool" ? "tool" : p.op, tool: p.tool, ok: true, packages: pk, checks: system ? [] : checks, script,
        steps: system ? [{ id: "root", phase: 1, actor: "root", title: "apt-get install (one sudo prompt)", commands: ["sudo -A bash <script below>"] }]
          : [{ id: p.op, phase: 1, actor: "odoo19", title: p.op === "validate" ? "Import odoo and the top-level modules of 4 required package(s)" : `Install ${pk.length} package(s) into ${p.root}/venv`,
              commands: [p.op === "validate" ? `${p.root}/venv/bin/python -c <import check script> ${p.root}/odoo ${pk.join(" ")}` : `CFLAGS=... /usr/lib/odoo-dev-panel/bin/uv pip install --python ${p.root}/venv/bin/python ${pk.join(" ")}`] }] };
    },
    "python.run": (p) => job("python", p.op === "validate" ? ["validate"] : p.op === "tool" && p.tool !== "debugpy" ? ["download", "root"] : ["pip"],
      p.op === "validate" ? { kind: "validate", ok: false, failed: ["pandas"], validation: { python: "3.12.14", odoo: { ok: true, version: "19.0" }, modules: { lxml: { ok: true, error: null }, babel: { ok: true, error: null }, pandas: { ok: false, error: "pandas: ImportError: numpy.core.multiarray failed to import" } } } }
        : { kind: p.op === "tool" ? "tool" : "install", ok: true, exit_code: 0 }),

    "modules.center": (p) => modCenter(p),
    "modules.plan": (p) => {
      const db = p.kind === "test" ? `odp_test_${p.modules[0]}_20261009_120000` : p.database;
      const argv = ["/opt/odoo19/venv/bin/python", "/opt/odoo19/odoo/odoo-bin", "-c", p.config, "-d", db, p.kind === "upgrade" ? "-u" : "-i", p.modules.join(","), "--stop-after-init",
        ...(p.kind === "test" ? ["--test-enable", "--test-tags", p.tags || p.modules.map((m: string) => `/${m}`).join(","), "--log-level=test", ...(p.demo ? ["--with-demo"] : [])] : [])];
      const bad = p.modules.includes("client_custom");
      const checks = p.modules.map((m: string) => ({ id: m, status: m === "client_custom" ? "fail" : "ok", detail: m === "client_custom" ? `${m} is in /opt/odoo19/custom/extensions/client_custom, which this config does not load` : `${m} found` }));
      const steps = [...(p.kind !== "test" && p.snapshot ? [{ id: "snapshot", phase: 1, actor: "agent", title: `Snapshot ${db} (database and filestore)`, commands: [`odp db snapshot /opt/odoo19 ${db}`] }] : []),
        { id: p.kind, phase: 2, actor: "odoo19", title: p.kind === "test" ? `Create ${db}, install ${p.modules.join(", ")} and run their tests` : `${p.kind === "install" ? "Install" : "Upgrade"} ${p.modules.join(", ")} in ${db}, then stop`, commands: [argv.join(" ")] },
        ...(p.kind === "test" ? [{ id: "cleanup", phase: 3, actor: "agent", title: "Drop the test database if every test passed; keep it if not", commands: [`odp db drop /opt/odoo19 ${db} --confirm ${db}  (only when passed)`] }] : [])];
      return { kind: p.kind, config: p.config, database: db, modules: p.modules, checks, steps, ok: !bad, argv, user: "odoo19", snapshot: p.snapshot, tags: p.tags, demo: p.demo };
    },
    "modules.run": (p) => {
      const db = p.kind === "test" ? `odp_test_${p.modules[0]}_20261009_120000` : p.database;
      const steps = p.kind === "test" ? ["test", "cleanup"] : p.snapshot ? ["snapshot", "upgrade"] : ["upgrade"];
      const fail = p.kind === "test" && p.modules.includes("exam_core");
      const extra = p.kind === "test" ? { status: fail ? "failed" : "passed", tests: 24, failures: fail ? 1 : 0, errors: 0, kept: fail, database: db, id: "t3",
        failed: fail ? [{ kind: "fail", test: "odoo.addons.exam_core.tests.test_marks.TestMarks.test_rounding" }] : [] } : { exit_code: 0, backup: p.snapshot ? `/opt/odoo19/odp-backups/snapshots/${db}-20261009-1200` : null, problems: [] };
      if (p.kind === "test") testRuns.unshift({ ...extra, at: new Date().toISOString(), installation: "/opt/odoo19", config: p.config, modules: p.modules, tags: p.tags, demo: p.demo, exit_code: 0, note: null });
      return job("modules", steps, { kind: p.kind, database: db, modules: p.modules, config: p.config, ...extra });
    },
    "modules.tests": () => testRuns,
    "modules.drop_test": (p) => { const r = testRuns.find((x) => x.id === p.id); if (r) r.kept = false; return job("db", ["drop"], {}); },
    "modules.scaffold_plan": (p) => ({ target: `${p.folder}/${p.name}`, ok: true, checks: [{ id: "folder", status: "ok", detail: `${p.folder} is writable` }],
      files: { "__init__.py": "from . import models\n", "__manifest__.py": `{\n    "name": "${p.name.replace(/_/g, " ")}",\n    "version": "19.0.1.0.0",\n    "license": "LGPL-3",\n    "depends": ${JSON.stringify(p.depends)},\n    "data": [\n        "security/ir.model.access.csv",\n        "views/views.xml",\n    ],\n}\n`, "models/__init__.py": "", "security/ir.model.access.csv": "id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink\n", "views/views.xml": "<odoo>\n</odoo>\n" } }),
    "modules.scaffold": (p) => ({ created: [], target: `${p.folder}/${p.name}` }),
    "modules.open": () => ({ argv: ["/usr/bin/code"] }),

    "repo.list": (p) => ({ repos: p.installation ? repos.filter((r) => r.installations.some((i: Json) => i.root === p.installation)) : repos, scan_roots: [] }),
    "repo.show": (p) => repos.find((r) => r.path === p.path),
    "repo.diff": (p) => {
      const r = repos.find((x) => x.path === p.path);
      const files = r?.state.dirty || r?.state.untracked ? [{ path: "adm_core/models/applicant.py", index: "", worktree: "M" }, { path: "adm_core/__manifest__.py", index: "", worktree: "M" }, { path: "notes.txt", index: "?", worktree: "?" }] : [];
      return { repo: p.path, files, truncated: false, diff: p.file ? `diff --git a/${p.file} b/${p.file}\n--- a/${p.file}\n+++ b/${p.file}\n@@ -12,7 +12,8 @@ class Applicant(models.Model):\n     _name = "adm.applicant"\n-    state = fields.Selection(STATES, default="draft")\n+    state = fields.Selection(STATES, default="new")\n+    merit = fields.Float()\n     name = fields.Char(required=True)\n` : null,
        incoming: (r?.state.behind ?? 0) > 0 ? [{ sha: "9ab31c0", author: "Ali", date: iso(90), subject: "Fix merit list rounding" }, { sha: "71de0f2", author: "Sara", date: iso(300), subject: "Add exam hall report" }] : [], outgoing: (r?.state.ahead ?? 0) > 0 ? [{ sha: "c0ffee1", author: "dev", date: iso(20), subject: "WIP attendance import" }] : [] };
    },
    "repo.plan": (p) => repoPlan(p),
    "repo.run": (p) => {
      const pl = repoPlan(p);
      if (!pl.ok) throw new Error(pl.checks.filter((c: Json) => c.status === "fail").map((c: Json) => c.detail).join("; ") || "nothing can run");
      const run_id = `run-${++runCounter}`;
      let t = 300;
      const results: Json[] = [];
      for (const it of pl.items) {
        if (it.skip) { results.push({ repo: it.repo, commands: [], status: it.level === "ok" ? "kept" : "skipped", output: "", problem: null, reason: it.skip, addons_path_entry: it.level === "ok" ? it.repo : undefined }); continue; }
        const broken = it.repo.endsWith("examinations") && p.op === "fetch";
        setTimeout(() => emit("git.step", { run_id, step: it.repo, status: "start", text: it.title }), t); t += 400;
        setTimeout(() => emit("git.step", { run_id, step: it.repo, status: "output", text: broken ? "git@git.example.com: Permission denied (publickey)." : "From git.example.com:aarsol/x\n   4f1c2aa..9ab31c0  main -> origin/main" }), t); t += 400;
        setTimeout(() => emit("git.step", { run_id, step: it.repo, status: broken ? "fail" : "ok", text: broken ? "Authentication failed" : "" }), t); t += 150;
        results.push(broken ? { repo: it.repo, commands: it.commands, status: "failed", output: "Permission denied (publickey).", reason: null, problem: prob("auth-failed", "error", "Authentication failed", "The remote refused your credentials. Check that your SSH agent has the right key, or that a Git credential helper is configured for this host.", ["ssh-add -l", "ssh -T git@<host>"]) }
          : { repo: it.repo, commands: it.commands, status: "ok", output: "", problem: null, reason: null, ...(p.op === "pull" ? { changed_files: 4, changed_modules: ["adm_core", "adm_portal"] } : {}), ...(p.op === "clone" || p.op === "bundle" ? { addons_path_entry: it.repo } : {}) });
      }
      const counts = { ok: 0, kept: 0, failed: 0, skipped: 0, cancelled: 0 } as Record<string, number>;
      for (const r of results) counts[r.status]++;
      setTimeout(() => emit("git.finished", { run_id, ok: true, error: null, op: p.op, results, counts }), t + 200);
      return { run_id, op: p.op, repos: pl.items.map((i: Json) => i.repo) };
    },
    "repo.cancel": () => ({ cancelling: true }),
    "desktop.pickFile": (p) => new Promise((r) => setTimeout(() => r({ path: p.kind === "archive" ? "/home/dev/Downloads/odoo_enterprise_19.0.latest.zip" : null }), 400)),
    "repo.register": (p) => ({ repo: p.path, assoc: { installation: p.installation, repo: p.path, ...p.fields } }),
    "repo.forget": () => ({ forgotten: true }),
    "repo.open": (p) => ({ argv: ["/usr/bin/code", p.path] }),

    "discover.adopt": (p) => { const i = installations.find((x) => x.root === p.root); if (i) i.adopted = !!p.adopt; return true; },
    "agent.start": async (p) => {
      if (!(await askPassword(p.user, "unlock"))) throw new Error("cancelled");
      agents = agents.map((a) => (a.user === p.user ? { ...a, state: "running", info: { pid: 7000, started_at: new Date().toISOString(), running_sessions: 0 } } : a));
      return true;
    },
    "agent.stop": (p) => { agents = agents.map((a) => (a.user === p.user ? { ...a, state: "stopped", info: undefined } : a)); return true; },
    "agent.enable": async (p) => {
      if (!(await askPassword(p.user, "enable"))) throw new Error("cancelled");
      agents = agents.map((a) => (a.user === p.user ? { ...a, enabled: true } : a));
      return { user: p.user, enabled: true };
    },
    "group.join": () => ({ group: "odoo-dev", exists: true, member: true, active: true }),
    "run.start": (p) => {
      const inst = instances.find((i) => i.path === p.instance)!;
      const owner = installations.find((i) => i.root === inst.installation)!.owner;
      const id = Math.random().toString(16).slice(2, 8);
      const port = p.http_port ?? Number(inst.options?.http_port ?? 8069) + 10;
      const argv = [...venv(inst.installation!), ...(p.shell ? ["shell", "--shell-interface=python"] : []), "-c", inst.path, ...(p.db ? ["-d", p.db] : []),
        ...(p.shell ? [] : ["--http-port", String(port)]), ...(p.update?.length ? ["-u", p.update.join(",")] : []), ...(p.install?.length ? ["-i", p.install.join(",")] : []),
        ...(p.stop_after_init ? ["--stop-after-init"] : []), ...(p.dev?.length ? ["--dev=" + p.dev.join(",")] : [])];
      sessions = [{ id, user: owner, name: p.shell ? `shell ${inst.name}` : inst.name, argv, pid: 60000 + Math.floor(Math.random() * 999), state: "running", started_at: new Date().toISOString(), ended_at: null, exit_code: null, adopted: false, log_size: 0, pty: !!p.shell, meta: { kind: p.shell ? "shell" : p.update?.length || p.install?.length ? "upgrade" : "server", db: p.db ?? null, port: p.shell ? null : port, instance: inst.path } }, ...sessions];
      return { id };
    },
    "run.open": () => true,
    "session.follow": (p) => { follow(p.user, p.id); return true; },
    "session.unfollow": (p) => { const f = followers.get(`${p.user}/${p.id}`); if (f?.timer) clearInterval(f.timer); followers.delete(`${p.user}/${p.id}`); return true; },
    "session.stop": (p) => {
      sessions = sessions.map((s) => (s.user === p.user && s.id === p.id ? { ...s, state: "exited", ended_at: new Date().toISOString(), exit_code: 0 } : s));
      const f = followers.get(`${p.user}/${p.id}`); if (f?.timer) clearInterval(f.timer);
      setTimeout(() => emit("session.ended", { user: p.user, id: p.id }), 50);
      return true;
    },
    "session.write": (p) => { emit("session.output", { user: p.user, id: p.id, offset: 0, data: p.data === "\x03" ? "^C\n>>> " : `${p.data}${p.data.trim() ? "<result>\n" : ""}>>> ` }); return true; },
    "session.resize": () => true,
    "session.problems": () => ({ counts: { DEBUG: 0, INFO: 412, WARNING: 3, ERROR: 2, CRITICAL: 0 }, start_offset: 0, first_error_line: 14, groups: [
      { id: "g1", level: "ERROR", logger: "odoo.http", title: "AttributeError: 'NoneType' object has no attribute 'name'", exception: "AttributeError", frame: { file: "/opt/odoo19/custom/nutech_sale/models/sale_order.py", line: 88, function: "_compute_margin_band" }, count: 2, first_line: 14, last_line: 220, first_time: "2026-10-08 09:04:52", last_time: "2026-10-08 09:31:02", dbs: ["nutech_prod"], sample: "Traceback (most recent call last):\n  File \"/opt/odoo19/custom/nutech_sale/models/sale_order.py\", line 88, in _compute_margin_band\nAttributeError: 'NoneType' object has no attribute 'name'" },
      { id: "g2", level: "WARNING", logger: "odoo.modules.loading", title: "The models ['x_legacy.report'] have no access rules in module nutech_hr", exception: null, frame: null, count: 3, first_line: 9, last_line: 190, first_time: "2026-10-08 09:00:07", last_time: "2026-10-08 09:20:07", dbs: ["nutech_prod"], sample: "The models ['x_legacy.report'] have no access rules in module nutech_hr, consider adding some" },
    ] }),
    "doctor.run": () => new Promise((r) => setTimeout(() => r({ findings, counts: { error: findings.filter((f) => f.severity === "error").length, warning: findings.filter((f) => f.severity === "warning").length, info: 1 }, not_checked: ["PostgreSQL of /opt/odoo18: agent locked"] }), 700)),
    "repair.plan": (p) => ({ root: p.root, version: "18.0", run_as: "odoo18", python: "3.11", venv: `${p.root}/venv`, requirements: [`${p.root}/odoo/requirements.txt`, ...(p.custom ? [`${p.root}/custom/requirements.txt`] : [])], extras: ["phonenumbers", "openpyxl"], carry_extras: p.carry_extras, ...plan([["python", "ok", "python3.11 found at /usr/bin/python3.11"], ["disk", "ok", "12 GB free"], ["agent", "warn", "odoo18 agent is locked: unlock asked at start"]], [["build", "Build venv.new with Python 3.11", ["/usr/bin/python3.11 -m venv /opt/odoo18/venv.new"]], ["install", "Install requirements", ["/opt/odoo18/venv.new/bin/pip install -r /opt/odoo18/odoo/requirements.txt"]], ["validate", "Import Odoo with the new venv", []], ["swap", "Swap venv.new into place, keep venv.bak-<time>", []]]) }),
    "repair.run": () => job("repair", ["build", "install", "validate", "swap"], { venv: "/opt/odoo18/venv", backup: "/opt/odoo18/venv.bak-20261008-1012" }),
    "perms.plan": (p) => ({ root: p.root, run_as: "odoo17", dev_user: "dev", changes: [{ path: "/etc/odoo/odoo17/acme.conf", kind: "file", current: "odoo17:odoo17 0644", target: "dev:odoo17 0640" }, { path: "/etc/odoo/odoo17", kind: "folder", current: "root:root 0755", target: "dev:odoo17 2750" }], kept: ["/etc/odoo/odoo17/demo.conf"], notes: [], script: "chown dev:odoo17 /etc/odoo/odoo17/acme.conf\nchmod 0640 /etc/odoo/odoo17/acme.conf\nchown dev:odoo17 /etc/odoo/odoo17\nchmod 2750 /etc/odoo/odoo17" }),
    "perms.run": async () => { await askPassword("dev", "permissions"); return job("repair", ["chown", "chmod"], { changed: 2, receipt: "/home/dev/.local/state/odoo-dev-panel/perms-20261008.json" }); },
    "db.list": (p) => p.root === "/opt/odoo18" ? { databases: [], error: null, recipes: [], agent_running: false } : { databases: dbs[p.root] ?? [], error: null, recipes: ["default", "aarsol-cleanup"], agent_running: true },
    "db.snapshots": (p) => ({ snapshots: snapshots[p.root] ?? [], error: null }),
    "db.plan": (p) => {
      const destructive = ["drop", "neutralize", "revert"].includes(p.action);
      const checks: [string, string, string][] = [["agent", "ok", "odoo agent running"], ["source", "ok", `${p.source ?? p.backup ?? "?"} exists`]];
      if (["clone", "restore"].includes(p.action)) checks.push(["target", p.target ? "ok" : "fail", p.target ? `${p.target} is free` : "name the new database"]);
      if (destructive) checks.push(["confirm", p.confirm === p.source ? "ok" : "fail", p.confirm === p.source ? "confirmed" : `type ${p.source} to confirm`]);
      if (p.action === "neutralize" && p.source?.includes("prod")) checks.push(["production-name", "warn", "the name looks like a production database"]);
      return { kind: p.action, root: p.root, run_as: "odoo19", recipe_sum: null, ...plan(checks, [["dump", `pg_dump ${p.source ?? ""}`, [`pg_dump -Fc -f /tmp/x.dump ${p.source ?? ""}`]], ["filestore", "Copy the filestore", [`cp -a …/filestore/${p.source ?? ""} …`]]]) };
    },
    "db.run": (p) => job("db", ["dump", "filestore"], p.action === "backup" || p.action === "snapshot" ? { backup: `/opt/odoo19/odp-backups/${p.source}-20261008-1015` } : p.action === "drop" ? { trash: `…/filestore/.trash-${p.source}` } : {}),
    "docker.list": () => ({ containers, error: null, available: true, versions: ["16.0", "17.0", "18.0", "19.0"], stacks_root: "/home/dev/odp-docker" }),
    "docker.plan": (p) => ({ kind: p.action, container: p.container, ...plan([["docker", "ok", "daemon reachable"], ...(p.action === "upgrade" ? [["database", p.database ? "ok" : "fail", p.database ? `${p.database} exists` : "pick a database"] as [string, string, string]] : [])], [["exec", `${p.action} ${p.container}`, [`docker ${p.action === "upgrade" ? "exec" : p.action} ${p.container}`]]]) }),
    "docker.run": () => job("docker", ["exec"]),
    "docker.logs": () => ({ text: LOG("shop").repeat(3), analysis: { groups: [{ id: "x", level: "ERROR", title: "AttributeError: 'NoneType' object has no attribute 'name'", count: 3, logger: "odoo.http", sample: "Traceback…", last_time: "2026-10-08 09:04:52" }] } }),
    "docker.shell": (p) => ({ command: `docker exec -it ${p.container} odoo shell -d ${p.database} --no-http` }),
    "docker.new_plan": (p) => ({ name: p.name, version: p.version, port: p.port ?? 18070, folder: `/home/dev/odp-docker/${p.name}`, kind: "new", container: "", ...plan([["name", /^[a-z0-9_-]+$/.test(p.name) ? "ok" : "fail", "lowercase letters, digits, - and _"], ["port", "ok", `${p.port ?? 18070} is free`]], [["folder", "Create the stack folder", [`mkdir -p /home/dev/odp-docker/${p.name}`]], ["compose", "Start the stack", ["docker compose up -d"]]]) }),
    "docker.new_run": () => job("docker", ["folder", "compose"]),
    "docker.delete_plan": (p) => ({ kind: "delete", container: p.container, ...plan([["confirm", p.confirm === "shop18" ? "ok" : "fail", "type the stack name"]], [["down", "docker compose down -v", ["docker compose down -v"]]]) }),
    "docker.delete_run": () => job("docker", ["down"]),
    "services.list": () => services,
    "services.action": async (p) => {
      if (!(await askPassword("dev", "service"))) throw new Error("sudo: a password is required");
      services = services.map((s) => (s.name !== p.name ? s : {
        ...s,
        active_state: p.action === "stop" ? "inactive" : ["start", "restart"].includes(p.action) ? "active" : s.active_state,
        sub_state: p.action === "stop" ? "dead" : ["start", "restart"].includes(p.action) ? "running" : s.sub_state,
        enabled: p.action === "enable" ? "enabled" : p.action === "disable" ? "disabled" : s.enabled,
      }));
      return { output: "" };
    },
    "services.journal": (p) => ({ text: `2026-10-08T08:59:58+0200 host systemd[1]: Started ${p.name}.\n` + LOG("legacy").split("\n").map((l) => (l ? `2026-10-08T09:00:01+0200 host odoo-bin[2881]: ${l}` : l)).join("\n") }),
    "services.show": (p) => { const s = services.find((x) => x.name === p.name)!; return { name: s.name, path: s.path, text: `[Unit]\nDescription=Odoo (${s.user})\nAfter=network.target postgresql.service\n\n[Service]\nUser=${s.user}\nExecStart=${s.exec_start}\nRestart=on-failure\n\n[Install]\nWantedBy=multi-user.target\n` }; },
    "config.open": (p) => {
      const inst = instances.find((i) => i.path === p.path) ?? instances[0];
      return { path: p.path, text: CONFIG(inst, !!p.reveal), sha: "abc", access: { owner: "dev", group: "odoo17", mode: "0640", writable: !p.path.includes("odoo16"), others_read: p.path.includes("acme") }, issues: p.path.includes("staging") ? [{ level: "error", key: "addons_path", text: "/opt/odoo18/custom/hr does not exist" }] : [] };
    },
    "config.form": (p) => {
      let text: string = p.text;
      for (const [k, v] of Object.entries(p.changes ?? {})) {
        const re = new RegExp(`^${k}\\s*=.*$`, "m");
        text = v === null ? text.replace(re, "").replace(/\n\n+/g, "\n") : re.test(text) ? text.replace(re, `${k} = ${v}`) : text + `${k} = ${v}\n`;
      }
      const o = parseConf(text);
      return { text, options: text.includes("[options]") ? o : null, issues: [], addons: (o.addons_path ?? "").split(",").filter(Boolean).map((path: string) => ({ path: path.trim(), state: path.includes("custom") && path.includes("18") ? "missing" : "ok", installation: null, version: null })) };
    },
    "config.save": () => ({ changed: true, backup: "/etc/odoo/.odp-backup/acme.conf.20261008-1020" }),
    "config.copy": (p) => ({ path: `/etc/odoo/odoo17/${p.name}.conf`, warning: null }),
    "modules.graph": (p) => {
      const mods: Record<string, any> = {
        base: { path: "/opt/x/odoo/addons/base", addons_path: "/opt/x/odoo/addons", depends: [], required_by: ["web", "mail", "sale"], installable: true, name: "Base", version: "19.0.1.3", db_state: p.database ? "installed" : undefined },
        web: { path: "/opt/x/odoo/addons/web", addons_path: "/opt/x/odoo/addons", depends: ["base"], required_by: ["mail"], installable: true, version: "19.0.1.0", db_state: p.database ? "installed" : undefined },
        mail: { path: "/opt/x/odoo/addons/mail", addons_path: "/opt/x/odoo/addons", depends: ["base", "web"], required_by: ["sale"], installable: true, version: "19.0.1.15", db_state: p.database ? "installed" : undefined },
        sale: { path: "/opt/x/odoo/addons/sale", addons_path: "/opt/x/odoo/addons", depends: ["base", "mail"], required_by: ["nutech_sale"], installable: true, version: "19.0.1.2", db_state: p.database ? "installed" : undefined },
        nutech_sale: { path: "/opt/x/custom/nutech_sale", addons_path: "/opt/x/custom", depends: ["sale", "nutech_core"], required_by: [], installable: true, version: "19.0.2.1", db_state: p.database ? "to upgrade" : undefined, db_version: "19.0.2.0", version_differs: !!p.database },
      };
      const focus = p.module ? { name: p.module, module: mods[p.module], needs: p.module === "nutech_sale" ? { sale: 1, mail: 2, base: 3, web: 3 } : { base: 1 }, needed_by: p.module === "base" ? { web: 1, mail: 1, sale: 1, nutech_sale: 2 } : {}, missing: p.module === "nutech_sale" ? { nutech_sale: ["nutech_core"] } : {} } : undefined;
      return { modules: mods, missing: { nutech_sale: ["nutech_core"] }, shadowed: [], unreadable: [], cycles: [], addons_paths: ["/opt/x/odoo/addons", "/opt/x/custom"], focus, installation: "/opt/odoo19", series: "19.0", extra: p.extra ?? [], db_error: null, db_only: p.database ? ["x_legacy_report"] : undefined, db_counts: p.database ? { installed: 4, "to upgrade": 1 } : undefined };
    },
    "compare.run": (p) => ({ a: { path: p.path, name: "acme", notes: [] }, b: { path: p.other, name: "demo", notes: [] }, facts: [{ key: "Odoo version", a: "17.0", b: "17.0", same: true }, { key: "git commit", a: "4f1c2aa", b: "4f1c2aa", same: true }, { key: "Python", a: "3.10", b: "3.10", same: true }], packages: { only_a: {}, only_b: {}, changed: {} }, options: { only_a: { workers: "4" }, only_b: {}, changed: { http_port: ["8069", "8070"], db_name: ["acme_prod", "False"] } }, addons: { only_a: ["/opt/odoo17/custom"], only_b: [] }, expected: ["http_port", "db_name"] }),
    "compare.installations": (p) => ({ a: { path: p.a, name: p.a, notes: [] }, b: { path: p.b, name: p.b, notes: [] }, facts: [{ key: "Odoo version", a: "17.0", b: "19.0", same: false }, { key: "Python", a: "3.10", b: "3.12", same: false }], packages: { only_a: { "pypdf2": "1.26.0" }, only_b: { "pypdf": "4.2.0" }, changed: { lxml: ["4.9.2", "5.2.1"] } }, options: { only_a: {}, only_b: {}, changed: {} }, addons: { only_a: [], only_b: [] }, expected: [] }),
    "compare.databases": () => ({ a: { root: "/opt/odoo19", database: "nutech_prod" }, b: { root: "/opt/odoo19", database: "nutech_upgrade_test" }, modules: { only_a: { x_legacy_report: "installed 19.0.1.0" }, only_b: {}, changed: { nutech_sale: ["installed 19.0.2.0", "installed 19.0.2.1"] } }, counts: { a: 215, b: 214 } }),
    "provision.plan": (p) => provisionPlan(p),
    "provision.run": async (p) => { const pl = provisionPlan(p); await askPassword(pl.spec.run_as, "provision"); return job("provision", ["root-script", "clone", "python", "config", "verify"], { root: pl.spec.root, run_as: pl.spec.run_as, conf_path: pl.spec.conf_path }); },
    "profile.list": () => ({ profiles: Object.entries(profiles).map(([name, d]) => ({ name, path: `/home/dev/.config/odoo-dev-panel/profiles/${name}.toml`, error: null, title: d.name, description: d.description ?? null, odoo_version: d.odoo_version ?? null, repos: (d.repos ?? []).length + (d["repos+"] ?? []).length, has_install: !!d.install })), folder: "/home/dev/.config/odoo-dev-panel/profiles", org: { path: "/home/dev/.config/odoo-dev-panel/org.toml", exists: true, error: null } }),
    "profile.resolve": (p) => resolveProfile(p.profile, p.version, p.overrides),
    "profile.read": (p) => ({ name: p.name, path: "", data: profiles[p.name], text: toml(profiles[p.name]) }),
    "profile.save": (p) => { profiles[p.name] = p.data; return { path: `/home/dev/.config/odoo-dev-panel/profiles/${p.name}.toml` }; },
    "profile.delete": (p) => { delete profiles[p.name]; return { moved_to: `/home/dev/.config/odoo-dev-panel/profiles/.trash-${p.name}-20261009-1200.toml` }; },
    "profile.import": () => ({ path: "/home/dev/.config/odoo-dev-panel/profiles/shared.toml" }),
    "profile.org": (p) => ({ path: p.path ?? "/home/dev/.config/odoo-dev-panel/org.toml" }),
    "profile.export": (p) => {
      const data = { name: `${p.root.split("/").pop()} setup`, description: `Exported from ${p.root} on 2026-10-09`, odoo_version: 19, config: { workers: "2" },
        repos: repos.filter((r) => r.path.startsWith(p.root + "/custom")).map((r) => ({ name: r.name, url: r.state.remotes.origin, branch: r.state.branch, destination: r.installations[0].relative })).filter((r) => r.url) };
      if (p.name) profiles[p.name] = data;
      return { data, text: toml(data), notes: ["custom/extensions/client_custom: no remote (or a URL with credentials), left out", "config options taken from /etc/odoo/odoo19/nutech.conf (safe options only; passwords, database and ports are never exported)"], path: p.name ? `/home/dev/.config/odoo-dev-panel/profiles/${p.name}.toml` : null };
    },
    "repo.addons_plan": (p) => instances.filter((i) => i.installation === p.installation).map((i, n) => ({ path: i.path, current: (i.options.addons_path ?? "").split(","), add: p.repos, sha: "abc", writable: n !== 1, error: null })),
    "repo.addons_apply": (p) => ({ path: p.path, changed: true, backup: `${p.path}.bak-20261009-120000` }),
  };

  return (message: Json) => {
    if (message.method === undefined) {
      const resolve = pending.get(message.id);
      if (resolve) { pending.delete(message.id); resolve(message.result); }
      return;
    }
    const handler = handlers[message.method];
    if (!handler) return fail(message.id, `mock: ${message.method} is not implemented`);
    Promise.resolve()
      .then(() => handler(message.params ?? {}))
      .then((result) => reply(message.id, result ?? null))
      .catch((e) => fail(message.id, String(e?.message ?? e)));
  };
}
