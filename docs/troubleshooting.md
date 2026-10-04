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

## Logs and receipts

| What | Where |
|---|---|
| Odoo output of a session | the Output panel, `odp logs -u <user> <id>` |
| Grouped warnings and errors of a session | Output panel, Problems; `odp logs -u <user> -p <id>` |
| Agent state and session logs | `~/.local/state/odoo-dev-panel/` of the run-as user |
| Provision receipt | `<installation root>/.odp-provision.json` |
| Repair and database receipts | `~/.local/state/odoo-dev-panel/{repairs,db}/` of your user |

Receipts and logs never contain passwords. When you report a bug, attach the receipt and the output of `odp --json doctor`, after checking them for client names you do not want to share.
