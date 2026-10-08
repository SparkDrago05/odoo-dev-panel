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

  const handlers: Record<string, (p: Json) => Json | Promise<Json>> = {
    "app.info": () => ({ version: "0.6.0-dev", user: "dev", socket_dir: "/run/odoo-dev-panel", pid: 1, group: { group: "odoo-dev", exists: true, member: true, active: true } }),
    "agents.list": () => agents,
    "sessions.list": () => [...sessions].sort((a, b) => b.started_at.localeCompare(a.started_at)),
    "discover.scan": () => snapshotOf(),
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
    "provision.plan": (p) => ({ spec: { ...p, run_as: p.run_as || `odoo${p.version}`, root: `/opt/odoo${p.version}`, conf_path: `/etc/odoo/odoo${p.version}/${p.config_name}.conf` }, preflight: [{ id: "root-path", status: installations.some((i) => i.version === `${p.version}.0`) ? "warn" : "ok", detail: `/opt/odoo${p.version}` }, { id: "python", status: "ok", detail: "python3.12 available" }, { id: "postgresql", status: "ok", detail: "PostgreSQL 16 running" }], ok: true, steps: [{ id: "user", phase: 1, actor: "root", title: `Create user odoo${p.version}`, commands: [] }, { id: "clone", phase: 2, actor: `odoo${p.version}`, title: "Clone Odoo community", commands: [] }, { id: "venv", phase: 2, actor: `odoo${p.version}`, title: "Build the venv", commands: [] }], root_script: `useradd --system odoo${p.version}\nmkdir -p /opt/odoo${p.version}`, config: `[options]\nhttp_port = ${p.http_port ?? 8069}\n` }),
    "provision.run": async (p) => { await askPassword(`odoo${p.version}`, "provision"); return job("provision", ["user", "clone", "venv"], { root: `/opt/odoo${p.version}`, run_as: `odoo${p.version}`, conf_path: `/etc/odoo/odoo${p.version}/${p.config_name}.conf` }); },
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
