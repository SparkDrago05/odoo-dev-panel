# Contributing

Bug reports, layout reports and pull requests are welcome.

## Before you start

- **Bugs:** open an issue with the Ubuntu release, the Odoo version, what you did, what happened, and the output of `odp --json doctor` (check it for names you do not want to share first). Security problems go through [SECURITY.md](SECURITY.md), not issues.
- **Your layout is not discovered correctly?** That is a bug worth reporting. Describe the folder structure (where the source, venv, configs and filestore live, which Linux user runs Odoo, how it connects to PostgreSQL). Discovery must work on any layout; provisioning may stay opinionated.
- **Larger changes:** open an issue first to agree on the approach.

## Licensing

The project is licensed under [Apache-2.0](LICENSE). By submitting a contribution you agree that it is licensed under the same terms (section 5 of the license). There is no CLA.

## Development setup (Ubuntu 24.04 / 26.04)

```sh
sudo apt install build-essential libwebkit2gtk-4.1-dev libsoup-3.0-dev \
    libjavascriptcoregtk-4.1-dev librsvg2-dev libayatana-appindicator3-dev libssl-dev postgresql
```

- Rust (user-level): https://rustup.rs
- uv (user-level): https://docs.astral.sh/uv/getting-started/installation/
- Node 22 and pnpm 9

Run from the source tree, no `.deb`:

```sh
sudo spike/setup-dev-host.sh     # once: group odoo-dev, test user odoo99, /run/odoo-dev-panel, /opt/odoo-dev-panel
# log out and back in so the odoo-dev group applies
./dev.sh                         # sync the core, start the app with hot reload
./dev.sh test                    # core unit tests
./dev.sh odp agent status        # CLI from the synced copy
./dev.sh shell                   # shell with the odp alias and ODP_* variables
```

`dev.sh` copies the core to `/opt/odoo-dev-panel/dev` on every start, because the run-as users cannot read a checkout under your home. UI changes reload by themselves.

Build the `.deb`:

```sh
packaging/build-runtime.sh       # standalone CPython + core into packaging/build/
cd app && pnpm install && pnpm tauri build
# -> app/src-tauri/target/release/bundle/deb/
```

## Repository

| Path | Content |
|---|---|
| `core/` | Python core and `odp` CLI. Standard library only: no third-party runtime dependencies |
| `app/` | Tauri v2 + React + TypeScript desktop app. A thin client of the core over JSON-RPC. `pnpm dev` in a plain browser answers from the fixtures in `app/src/dev/mock.ts`, no core needed |
| `packaging/` | Runtime build, `.deb` maintainer scripts, tmpfiles.d, APT repository build (`packaging/apt/`) |
| `website/` | Project website (GitHub Pages): landing page, docs rendered from the Markdown files. `python3 website/build.py website/_site` builds it locally (needs the `markdown` package) |
| `spike/` | Container acceptance tests and dev host setup |
| `docs/` | User docs |

## Tests

Every change needs the checks CI runs:

```sh
./dev.sh test                                        # core unit tests
uvx ruff check core/src core/tests --select F        # unused imports and the like
cd app && pnpm exec tsc --noEmit && pnpm test        # frontend types and unit tests
```

Changes to a feature also need its container test. They use LXD (`sudo snap install lxd && lxd init --auto`) and fresh Ubuntu 24.04 and 26.04 containers, and never touch your machine:

| Test | Covers |
|---|---|
| `spike/discover-test.sh` | discovery and adopt on three layouts |
| `spike/layouts-test.sh` | a `$HOME` clone and Odoo's .deb layout: discover, databases, run |
| `spike/db-test.sh` | backup, restore, clone, drop, neutralize |
| `spike/provision-test.sh`, `spike/repair-test.sh` | provision and venv repair (need the `.deb`) |
| `spike/git-test.sh`, `spike/profile-test.sh` | Git workspace, profiles and bundles |
| `spike/modules-test.sh`, `spike/python-test.sh` | module center, Python environment and dev tools |
| `spike/tasks-test.sh` | workflows, gates, retry |
| `spike/debug-test.sh` | debugpy presets with a real debugger attach, launch.json merge |
| `spike/perf-test.sh` | performance, database explorer, SQL log, logs of outside instances |

Run them as `script -qec "spike/<test>.sh" /dev/null > spike/.out/<test>.log`.

## Code rules

- The core is authoritative: the app calls the same operations as the CLI, and every feature has a CLI form.
- Every mutating action has a read-only plan with the exact commands, and changes only what the same job created.
- Never put passwords in command lines, logs, receipts, plans or the registry.
- Never delete user data. Move it aside (`.trash-*`, `.bak-*`) and let the user remove it.
- Keep the surrounding style: small functions, comments only where the reason is not obvious.

## Releasing (maintainers)

1. Set the same version in `app/src-tauri/tauri.conf.json`, `app/package.json`, `app/src-tauri/Cargo.toml`, `core/pyproject.toml` and `core/src/odoo_dev_panel/__init__.py`.
2. Commit, then tag and push: `git tag v<version> && git push origin v<version>`.
3. CI checks that the tag matches every version, builds and tests the `.deb`, and creates a **draft** release with the `.deb` and `SHA256SUMS`.
4. Review the draft on GitHub and publish it.
5. Publishing triggers the **Pages** workflow. It downloads the `.deb` of the five newest published releases and builds the signed APT repository (`packaging/apt/build-repo.sh`). It builds the website, deploys both to GitHub Pages, then installs the new version from the live repository on Ubuntu 24.04 and 26.04.

The APT signing key is the repository secret `APT_GPG_PRIVATE_KEY` (ASCII-armored, no passphrase). Its public half is `packaging/apt/odoo-dev-panel.asc`; the workflow refuses to sign when the two do not match. To rotate the key, replace both and tell users to download the new public key.
