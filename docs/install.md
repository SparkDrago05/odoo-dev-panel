# Install and first run

Odoo Dev Panel runs on Ubuntu 24.04 and 26.04 (x86_64). The package brings its own Python; the machine needs neither Python nor Rust for it.

## 1. Install

Download `odoo-dev-panel_<version>_amd64.deb` from the [releases page](https://github.com/SparkDrago05/odoo-dev-panel/releases), then:

```sh
sudo apt install ./odoo-dev-panel_*_amd64.deb
```

The package installs:

| Path | What |
|---|---|
| `/usr/bin/odoo-dev-panel` | the desktop app |
| `/usr/bin/odp` | the CLI (same core as the app) |
| `/usr/lib/odoo-dev-panel/` | bundled Python 3.12, the core, `uv` |
| `/run/odoo-dev-panel/` | agent sockets (recreated at boot, group `odoo-dev`) |
| `/opt/odoo-dev-panel/python/` | shared Python interpreters for Odoo venvs, made by provisioning |

It also creates the system group `odoo-dev`.

## 2. Join the `odoo-dev` group

Members of `odoo-dev` can talk to the agents. When you are not a member, the app says so on start and offers **Add me** (one sudo prompt); it then restarts its core under the new group, so no logout is needed. From a terminal:

```sh
sudo usermod -aG odoo-dev "$USER"     # then log out and back in, or run `newgrp odoo-dev` in that terminal
```

The Linux users that run Odoo (for example `odoo17`, or `odoo` for Odoo's own .deb) join the group from the app: the Agents list shows them with an **Enable** button, which runs `sudo usermod -aG odoo-dev <user>` after showing it to you. You do not need this for installations you run as yourself.

Membership in `odoo-dev` lets you start and stop processes as every enabled run-as user. See [SECURITY.md](../SECURITY.md).

## 3. First run

Start **Odoo Dev Panel** from the application menu, or `odoo-dev-panel` from a terminal.

1. **Installations.** The app scans `/opt`, `/srv`, `/usr/local/src` and your home for Odoo source trees, and `/etc/odoo`, `/etc/odoo.conf`, `/etc/odoo-server.conf`, `~/.odoorc` and the folders next to each tree for configs. Nothing is changed. Folders it could not read are listed under the scan.
2. **Adopt** the installations you want to keep in the list. Adopting writes only to the app's own registry (`~/.local/state/odoo-dev-panel/registry.json`), never to Odoo files.
3. **Unlock** the agent of each run-as user (Agents list). This asks for your sudo password once per user per boot and starts a small agent as that user. The agent starts and stops Odoo without further passwords.
4. **Run** an instance: pick a config and a database, optionally modules to install or upgrade, and start it. Output goes to a log file and the Output panel. Odoo keeps running when you close the app; only **Stop** ends it. An instance started outside the app (a terminal, an IDE, a systemd unit) shows its log on its page when its config sets an absolute `logfile` you can read, or from its unit's journal (CLI: `odp logs --config CONFIG -f`).
5. **Doctor** runs read-only checks (broken venvs, configs, ports, systemd units, filestores) and offers a repair where one exists.
6. **Configs:** **Edit** next to a config (Installations panel) opens it with passwords masked, validates it as you type (unknown options, bad ports, missing addons paths, port clashes) and saves it with a backup. **Copy to new config** makes a config for another client from an existing one.
7. **Modules** and **Compare** (buttons next to **Edit** on each config): **Modules** shows what a module depends on, what requires it, and missing, shadowed or cyclic modules, without importing any code. Click a module for a picture of what it needs (left) and what needs it (right), limited by depth. Choose a database under **State in database** to see each module's state in it, modules installed at another version than the manifest (typical after moving a database to a newer Odoo series: run `-u` on them) and modules the database holds that the addons_path cannot find. **Also look in a folder** adds a folder of modules the config does not load. **Compare** diffs two configs, two installations as a whole, or the installed modules of two databases (state and version): version, git commit, Python, venv packages, addons paths and options. CLI: `odp modules CONFIG [MODULE]`, `odp compare A B`. Database states and database compare are in the app.
8. **Databases** backs up, restores, clones (with filestore and an optional neutralization recipe) and drops databases. Every action shows its plan and commands first. **Snapshot** is a backup kept in the run-as user's `~/odp-backups/snapshots`, with only the newest few per database kept (3 by default). **Revert** replaces a database with a snapshot and keeps the replaced database and filestore as `<db>_before_<time>`. In **Run**, tick **Snapshot first** to snapshot before a `-u` upgrade. CLI: `odp db snapshot`, `odp db snapshots`, `odp db revert`, `odp db forget`, `odp start --snapshot`.

9. **Tasks** runs saved workflows of typed operations: pull or fetch repositories, snapshot, back up, clone, neutralize, restore, revert or drop a database, upgrade, install or test modules, validate or install Python packages, start or stop an instance, and a guarded command step. Three recipes come built in: **Safe upgrade** (snapshot, upgrade, tests in a throwaway database), **Pull and upgrade** and **Neutralized test copy**; copy one to change it. **Run…** asks for the parameters, then shows every step with its checks, the exact commands and who runs them (you, or the installation's run-as user through its agent). Steps that change data wait for your confirmation unless the workflow marks them `auto = true`; a command step and a drop always wait. Each step is planned again just before it runs, and the first failure stops the run. **Retry…** in the run history starts again at the failed step with the same parameters, after planning it again; a workflow changed since the run cannot be retried. Workflows are TOML files in `~/.config/odoo-dev-panel/workflows/`; the history is in `~/.local/state/odoo-dev-panel/tasks/history.jsonl` (titles, statuses, commands, no output). CLI: `odp tasks list|show|ops|preview|run|retry|history|import|delete`.

10. **Debug** (tab on each config's page) starts Odoo under `debugpy` as the run-as user, through its agent, so VS Code can attach. A **preset** remembers the database, `-u`/`-i`, `--dev` flags and a fixed debugpy port (the first free one from 5678), or modules to test under the debugger in a throwaway database (dropped when the tests pass, kept when they fail). Debug runs add `--workers=0 --max-cron-threads=0`, so requests are served by the process the debugger is in; the config file is not changed. Install debugpy first from the Python tab. **VS Code launch.json…** writes one attach entry per preset (`Odoo: <name>`) into `<installation>/.vscode/launch.json`: you see the diff first, other entries are kept, an entry of the same name is replaced only if you tick it, a file with comments is never rewritten (you get the entries to paste), and the previous file is kept as `launch.json.bak-TIME`. Open the installation folder as the VS Code workspace and start the entry. **Run** also has a **Debug with debugpy** option, and the Tasks step `instance.start` takes `debug_port`. In any log, a traceback line `File "…", line N` opens that file at that line in your IDE (`code -g`, PyCharm `--line`; `ODP_IDE` picks the editor). While a debug session runs, any local user can connect to its port and run code as the run-as user: use it on a machine you do not share. CLI: `odp debug list|add|edit|delete|plan|start|vscode|open`, `odp start --debug-port N [--debug-wait]`.

11. **Performance** shows what slows an installation down right now: each Odoo process tree's CPU (over one second) and memory, PostgreSQL sessions with their state, running time, wait event and the sessions blocking them, lock counts, connections against `max_connections`, and a short verdict (Odoo busy, too many connections, a long query, a lock wait, a transaction left open, debug logging or `--dev` slowing pages, `db_maxconn` × workers above `max_connections`). Everything is read as the installation's own PostgreSQL role, which sees its own sessions fully and other roles' sessions without their query text; **Grant pg_monitor…** runs one reviewed sudo script so the role can read every session (read-only statistics, not superuser; it can be revoked the same way). **Cancel query** stops a running statement of the role's own sessions after you confirm (`pg_cancel_backend`; connections are never terminated). Tick **Log SQL** in Run (or `odp start --log-sql`) and the session's Problems view groups every query by statement, with count and time. In **Databases**, **Models, external IDs, size…** on a database opens a read-only explorer: models with their fields and the relations in and out, external IDs, the largest tables (estimates) and exact row counts capped at 100000. CLI: `odp perf ROOT [--cancel PID] [--grant|--revoke]`, `odp db models|model|xmlids|sizes|count ROOT DB`, `odp logs -u USER ID --sql`.

**Docker.** With nothing set up yet, **New Docker Odoo…** (Installations panel, Docker section) creates a stack: pick a name and an Odoo version (15.0 to 20.0), optionally a port and your own addons folder. The app writes `~/odp-docker/<name>/` (compose file, a generated database password in a `0600` `.env`, the config, an addons folder), downloads the images and starts Odoo and PostgreSQL, reachable on `http://localhost:<port>` from this machine only. Then **New database…** on its row creates the first database. **Delete…** removes a stack the app made (containers, databases, folder) after you type its name; it never offers that for containers it did not create. If creating fails, what that run made is removed.

When `docker` is installed and you may use it (member of the `docker` group, which is root-equivalent; the app never adds you), the Installations panel also lists Odoo containers: official `odoo` images, custom images with `odoo` in the name, or containers that run `odoo-bin`. It shows the version, compose project, published ports and where the config, addons and data mounts are on the host. Listing only runs `docker ps` and `docker inspect`; it changes nothing, and secret environment values are never read into the app. Only containers it recognizes as Odoo can be acted on.

Per container: **Start**, **Stop** (up to 30 s for a clean shutdown), **Restart**, **Logs** (levels, tracebacks and repeats grouped, optional follow), **Upgrade…** (`-u` / `-i` inside the container through its entrypoint, so the database options come from the container's own environment; a stopped compose service runs once in a new container that is removed afterwards) and **Shell** (shows the `odoo shell` command to run in a terminal). Every action shows its plan first.

**Databases.** A container whose PostgreSQL runs in a container of the same compose project also appears in the Databases panel as "Docker container NAME", with the same actions as native installations: backup, restore, clone, snapshot, revert, neutralize, drop. Dumps and archives are written to `~/odp-backups/docker/NAME/` on this machine (mode 0750), in the same format as native backups, so one restores into the other. Database tools run with `docker exec` in the database container; the filestore is read and written by a short-lived helper container of the Odoo image with the Odoo container's mounts, so it works whether the container runs or not. Drop, revert and neutralize need no session on the database: stop the Odoo container first. Without a volume or bind mount for `/var/lib/odoo` the filestore lives inside the container and only the database is copied (the plan warns). Databases that PostgreSQL outside Docker holds are not handled.

**Doctor** adds: a stopped container whose host port another process holds, a running container whose database container is stopped, a container that exited with an error, bind mounts that do not exist on the host, a read-only data folder, a config the container's user cannot read, `addons_path` entries missing on the host, and mounted Odoo source of another version than the image.

CLI: `odp docker [list|new|delete|start|stop|restart|upgrade|logs|shell]`, and `odp db ... docker:NAME` for databases (for example `odp db snapshot docker:shop-web-1 mydb`).

### Config permissions

A config holds database and admin passwords. The recommended permissions, which Provision creates and Doctor checks (H10, H13):

| What | Owner | Group | Mode |
|---|---|---|---|
| config file | you (the developer) | the run-as user's group | `0640` |
| config file, when you are the run-as user | you | you | `0600` |
| folder used by one installation only, e.g. `/etc/odoo/odoo17/` | you | the run-as user's group | `2750` |

You can edit without sudo, Odoo reads through the group, nobody else reads the passwords, and the setgid folder gives new configs the right group. Shared folders such as `/etc/odoo` are never changed. **Fix permissions** (Doctor or the editor), or `odp repair config-perms <root>`, applies this with one reviewed sudo script and keeps the old owners and modes in a receipt.

### Create a new installation instead

**New Odoo installation** (or `odp provision`) creates one from scratch: a run-as user, a pinned Python, a venv, the Odoo source, a PostgreSQL role and a first config. The recommended layout is `/opt/odooNN` with user `odooNN` and configs in `/etc/odoo/odooNN/`; every path and name can be changed. Root steps are one generated script that you review before it runs with a single `sudo` prompt.

Supported Odoo versions: 15, 16, 17, 18, 19, 20. PostgreSQL must be installed (`sudo apt install postgresql`).

## 4. The same from the CLI

Everything the app does is available through `odp`. Add `--json` for machine-readable output.

```sh
odp discover                                 # installations, configs, databases, processes, ports
odp adopt /home/me/src/odoo-17.0             # remember an installation
odp agent start -u odoo17                    # unlock (sudo prompt)
odp start /etc/odoo/odoo17/client.conf -d client_db --dev xml
odp ps -u odoo17                             # sessions
odp logs -u odoo17 <id>
odp logs -u odoo17 -l error <id>              # errors and their tracebacks only
odp logs -u odoo17 -p <id>                    # warnings and errors grouped, with counts
odp stop -u odoo17 <id>
odp doctor
odp repair config-perms /opt/odoo17          # standard permissions for its configs (sudo)
odp config edit /etc/odoo/odoo17/client.conf # $EDITOR, passwords masked, validated, backup
odp config set /etc/odoo/odoo17/client.conf http_port=8070 --unset workers
odp config copy /etc/odoo/odoo17/client.conf client_b
odp db list /opt/odoo17
odp db clone /opt/odoo17 client_db client_db_test --neutralize
odp tasks preview safe-upgrade -p config=/etc/odoo/odoo17/client.conf -p database=client_db -p modules=sale_x
odp tasks run safe-upgrade -p config=/etc/odoo/odoo17/client.conf -p database=client_db -p modules=sale_x
odp tasks retry <run id>                     # from the failed step, same parameters
odp debug add web -c /etc/odoo/odoo17/client.conf -d client_db   # debugpy port 5678
odp debug start web                          # attach VS Code to 127.0.0.1:5678
odp debug vscode /opt/odoo17 --write         # attach entries into .vscode/launch.json
odp perf /opt/odoo17                         # what slows it down: Odoo CPU/memory, sessions, locks
odp db model /opt/odoo17 client_db res.partner   # fields and relations (read-only)
odp provision plan -V 18                     # dry run: checks, steps, root script
odp provision run -V 18
```

## 5. Neutralization recipes

Cloning with `--neutralize` runs the built-in `default` recipe on the clone only: crons and mail servers off, passwords reset. Put your own recipes in `~/.config/odoo-dev-panel/recipes/<name>.sql`; `{{database}}` is replaced by the target database name. Select them in the clone dialog or with `--neutralize <name>`.

## 6. Uninstall

```sh
sudo apt remove odoo-dev-panel      # running Odoo processes keep running
sudo apt purge odoo-dev-panel       # also removes /opt/odoo-dev-panel and /run/odoo-dev-panel
```

Removal never touches Odoo installations, configs, databases, filestores or backups. The `odoo-dev` group stays.

Problems: see [troubleshooting.md](troubleshooting.md).
