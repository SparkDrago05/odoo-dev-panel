# Troubleshooting

Run `odp doctor` first: it checks the known problems read-only and says what to do. The cases below are the ones it cannot fix for you.

## Agents

**"No version users found" / a run-as user is missing from the Agents list.**
The list shows users with an agent socket, system accounts in `odoo-dev`, and the owners of discovered installations. Check that the installation is discovered (Installations panel or `odp discover`). Regular (human) accounts in `odoo-dev` are not listed: they are developers, not run-as users.

**"not in odoo-dev; enable it first".**
Click **Enable** next to the user, or run `sudo usermod -aG odoo-dev <user>`. Then unlock again.

**"cannot open /run/odoo-dev-panel/<user>.sock: Permission denied".**
You are not in `odoo-dev`, or not in this session. The app shows a banner with **Add me**; it restarts its core under the group, so no logout is needed. For the CLI: `sudo usermod -aG odoo-dev "$USER"`, then `newgrp odoo-dev` in that terminal or log out and back in.

**Unlock asks for a password and then fails.**
The password is yours (sudo), not the run-as user's. Your account must be allowed to use `sudo`. The exact command is shown in the dialog; you can run it in a terminal to see the full error: `odp agent start -u <user>`.

**The agent is gone after a reboot.**
Expected: agents run until shutdown, and you unlock them once per boot. Odoo processes started before the reboot are gone too.

## Running Odoo

**Odoo keeps running after I closed the app.**
By design. Reopen the app and the Sessions list shows it again, or use `odp ps` and `odp stop -u <user> <id>`.

**"no discovered instance".**
`odp start` takes a config path or a unique config name from `odp discover`. A config is linked to an installation by its `addons_path`; configs whose paths do not exist show up as "orphan configs".

**Port already in use.**
The app picks the config's `http_port`, or the next free port when it is busy. `odp start --port 8075` sets one for this run without changing the config.

**ModuleNotFoundError, or Odoo fails with the wrong Python.**
The venv is broken, often after a system Python upgrade. Doctor flags it ("venv broken"); **Repair** (or `odp repair venv <root>`) builds a new venv next to the old one, checks it, swaps it in, and keeps the old one as `venv.bak-<time>`.

## Configs

**The editor opens a config read-only.**
You cannot write the file (often `root:root 0644`). **Fix permissions** gives the installation's configs the standard owner, group and mode with one sudo prompt; see [install.md](install.md#config-permissions).

**"changed since you opened it".**
Someone or something else wrote the file after you opened it. Reopen it and apply your change again.

**Where is the previous version?**
Every save first writes `<config>.bak-<time>` (mode `0600`) next to the config, or, when the folder is shared and not yours (`/etc/odoo`), under `~/.local/state/odoo-dev-panel/config-backups/`.

**Copy to new config is refused.**
The folder is not writable for you. Fix permissions makes a folder that only this installation uses yours; a shared folder such as `/etc/odoo` stays root's, so create configs for that installation elsewhere, for example `/etc/odoo/<run-as user>/`.

## Databases

**"<role> uses peer authentication".**
The config has no `db_host` and no password, so PostgreSQL lets only the role's own Linux user in. Discovery runs as you and cannot list those databases. Unlock that user's agent: the Databases panel and `odp db` then query through it.

**"databases could not be listed: password authentication failed".**
The `db_user`/`db_password` in the config do not match the PostgreSQL role. Fix the config or the role password (`sudo -u postgres psql -c "ALTER ROLE <role> PASSWORD '...'"`).

**Filestore "unknown".**
The filestore lives in a folder you cannot read (another user's home or a private `data_dir`). Unlock the run-as user's agent; it checks the folder for you.

**Restore fails with `unrecognized configuration parameter "transaction_timeout"`.**
The `pg_dump`/`pg_restore` on PATH are newer than the server. Install the client of the server's version: `sudo apt install postgresql-client-<server major>`. The plan warns about this.

**Clone failed during the recipe.**
The clone is kept and marked NOT neutralized; the source is never changed. Fix the recipe and run **Neutralize** on the clone, or drop the clone.

**Where did a dropped database's files go?**
The filestore is moved to `<filestore base>/.trash-<db>-<time>`, never deleted. Remove it yourself when you no longer need it.

## Repositories

**A repository shows its owner's name and Fetch, Pull and Switch are disabled.**
It belongs to another Linux user (often the run-as user) and is shown read-only. The app reads it with a per-command `safe.directory` and never writes it. Pull as that user, or make the developer its owner.

**Pull skips a repository: "has uncommitted changes" or "diverged from …".**
Only fast-forward pulls run. Commit or stash your changes, or merge in a terminal; the app never resets, cleans or forces.

## Module tests and Python

**A test run failed and the `odp_test_*` database is still there.**
It is kept on purpose so you can look at it. Drop it from the test history (Modules tab) or with `odp modules drop-test <id>`.

**A package shows "wrong version" after installing it.**
Restart the Odoo processes of that installation: a running process keeps the old version loaded. Validate imports checks the venv as Odoo would load it.

## Tasks

**"workflow changed since that run; start a new run instead of a retry".**
Retry runs the exact workflow file of the failed run. Edited workflows start fresh.

**A step failed its checks in the preview but the workflow can still run.**
Later steps can depend on earlier ones (a clone a later step uses), so the preview may fail them. Each step is planned again just before it runs; a step whose checks still fail stops the run before anything runs.

**A command step fails with "is not in /usr/local/bin:/usr/bin:/bin".**
A step run as the run-as user needs a program on the system PATH or an absolute path in `argv`.

## Debugging

**"debugpy is not in <venv>".**
Install it from the installation's Python tab (Development tools) or `odp python tool debugpy --root <root>`.

**"something already listens on port 5678".**
Another debug session or program holds the preset's port. Stop it, or edit the preset's debugpy port (and write the VS Code entries again).

**Breakpoints in a request are not hit.**
Debug runs add `--workers=0`; if you started Odoo yourself with workers, requests run in forked children the debugger does not see. With `--dev=reload` Odoo restarts itself outside the debugger after a file change: start the preset again.

**VS Code launch.json is not written: "has comments or trailing commas".**
The file is JSONC and is never rewritten, so your comments survive. Paste the entries the dialog shows (or `odp debug vscode <root>` prints) into its `configurations`.

**Clicking a traceback line does nothing.**
No IDE was found. Set `ODP_IDE` (for example `ODP_IDE=code` or `ODP_IDE=pycharm`) in the environment the app starts with.

## Performance

**Other sessions show "(query hidden: another role)".**
The installation's role sees only its own sessions' queries. **Grant pg_monitor…** (or `odp perf <root> --grant`) lets it read every session; `--revoke` takes it back. On a PostgreSQL server on another machine, ask its administrator.

**"session N is not a running query of this installation's role".**
Cancel only works on an active query of the role's own sessions. Other roles' queries are cancelled by their owner or the PostgreSQL administrator.

**An instance started outside the app shows no log.**
Its output went to the terminal or IDE that started it. Set an absolute `logfile` in its config that you can read (for example in the installation folder), or run it as a systemd unit, and its log appears on its page.

## Logs and receipts

| What | Where |
|---|---|
| Odoo output of a session | the Output panel, `odp logs -u <user> <id>` |
| Grouped warnings and errors of a session | Output panel, Problems; `odp logs -u <user> -p <id>` |
| Agent state and session logs | `~/.local/state/odoo-dev-panel/` of the run-as user |
| Provision receipt | `<installation root>/.odp-provision.json` |
| Repair and database receipts | `~/.local/state/odoo-dev-panel/{repairs,db}/` of your user |
| Workflow run history | `~/.local/state/odoo-dev-panel/tasks/history.jsonl` |
| Module test history | `~/.local/state/odoo-dev-panel/module-tests.json` |
| Workflows, profiles, debug presets | `~/.config/odoo-dev-panel/{workflows/,profiles/,debug-presets.json}` |
| Grouped SQL queries of a `--log-sql` run | Problems view; `odp logs -u <user> --sql <id>` |

Receipts and logs never contain passwords. When you report a bug, attach the receipt and the output of `odp --json doctor`, after checking them for client names you do not want to share.
