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

Members of `odoo-dev` can talk to the agents. Add yourself, then **log out and back in** (a new terminal is not enough):

```sh
sudo usermod -aG odoo-dev "$USER"
```

The Linux users that run Odoo (for example `odoo17`, or `odoo` for Odoo's own .deb) join the group from the app: the Agents list shows them with an **Enable** button, which runs `sudo usermod -aG odoo-dev <user>` after showing it to you. You do not need this for installations you run as yourself.

Membership in `odoo-dev` lets you start and stop processes as every enabled run-as user. See [SECURITY.md](../SECURITY.md).

## 3. First run

Start **Odoo Dev Panel** from the application menu, or `odoo-dev-panel` from a terminal.

1. **Installations.** The app scans `/opt`, `/srv`, `/usr/local/src` and your home for Odoo source trees, and `/etc/odoo`, `/etc/odoo.conf`, `/etc/odoo-server.conf`, `~/.odoorc` and the folders next to each tree for configs. Nothing is changed. Folders it could not read are listed under the scan.
2. **Adopt** the installations you want to keep in the list. Adopting writes only to the app's own registry (`~/.local/state/odoo-dev-panel/registry.json`), never to Odoo files.
3. **Unlock** the agent of each run-as user (Agents list). This asks for your sudo password once per user per boot and starts a small agent as that user. The agent starts and stops Odoo without further passwords.
4. **Run** an instance: pick a config and a database, optionally modules to install or upgrade, and start it. Output goes to a log file and the Output panel. Odoo keeps running when you close the app; only **Stop** ends it.
5. **Doctor** runs read-only checks (broken venvs, configs, ports, systemd units, filestores) and offers a repair where one exists.
6. **Databases** backs up, restores, clones (with filestore and an optional neutralization recipe) and drops databases. Every action shows its plan and commands first.

### Create a new installation instead

**New Odoo installation** (or `odp provision`) creates one from scratch: a run-as user, a pinned Python, a venv, the Odoo source, a PostgreSQL role and a first config. The recommended layout is `/opt/odooNN` with user `odooNN` and configs in `/etc/odoo/odooNN/`; every path and name can be changed. Root steps are one generated script that you review before it runs with a single `sudo` prompt.

Supported Odoo versions: 15, 16, 17, 18, 19. PostgreSQL must be installed (`sudo apt install postgresql`).

## 4. The same from the CLI

Everything the app does is available through `odp`. Add `--json` for machine-readable output.

```sh
odp discover                                 # installations, configs, databases, processes, ports
odp adopt /home/me/src/odoo-17.0             # remember an installation
odp agent start -u odoo17                    # unlock (sudo prompt)
odp start /etc/odoo/odoo17/client.conf -d client_db --dev xml
odp ps -u odoo17                             # sessions
odp logs -u odoo17 <id>
odp stop -u odoo17 <id>
odp doctor
odp db list /opt/odoo17
odp db clone /opt/odoo17 client_db client_db_test --neutralize
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
