# Odoo Dev Panel

One control center for the Odoo development environments on an Ubuntu workstation: discover installations, diagnose and repair them, run and stop instances, and manage databases, however each installation was set up.

- **Discover** every Odoo source tree, venv, config, database, filestore, running process and systemd unit, without changing anything.
- **Run** an instance as its own Linux user, with a database, modules to install or upgrade, `--dev` flags or an interactive shell. Odoo keeps running when the app closes; only Stop ends it.
- **Doctor** finds broken venvs, unreadable or exposed configs, port clashes, failed units and missing filestores, and repairs a broken venv safely.
- **Databases:** back up, restore, clone with filestore, neutralize (default or your own SQL recipe) and drop, each with a plan you review first.
- **Provision** a new installation for Odoo 15–19 with a pinned Python, a venv, a PostgreSQL role and a first config.
- A desktop app and the `odp` CLI with the same features.

Status: pre-release. Ubuntu 24.04 and 26.04, x86_64.

## Install

```sh
sudo apt install ./odoo-dev-panel_*_amd64.deb
sudo usermod -aG odoo-dev "$USER"     # then log out and back in
```

Then start **Odoo Dev Panel** from the application menu. Details and first steps: [docs/install.md](docs/install.md).

## How it works

```
Desktop app (Tauri) ── JSON-RPC on stdio ──> odp sidecar (Python, as you)
                                               │ Unix socket /run/odoo-dev-panel/<user>.sock
                                               ▼
              sudo systemd-run --uid=odoo17 ──> odp agent (as the run-as user, one per user)
                                               │ own session, output to a log file
                                               ▼
                                          odoo-bin (survives app close and crashes)
```

You unlock each run-as user once per boot (one sudo prompt). Its agent then starts and stops Odoo without further passwords. Security model: [SECURITY.md](SECURITY.md).

## Documentation

- [Install and first run](docs/install.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Security](SECURITY.md)
- [Contributing](CONTRIBUTING.md): development setup, tests, code rules

## License

[Apache-2.0](LICENSE).
