# Security

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub: **Security → Report a vulnerability** on this repository. Do not open a public issue. You should get an answer within a week. Fixes are released as soon as they are ready and credited unless you ask otherwise.

## Trust model

Odoo Dev Panel is a tool for one developer's own workstation. It is not a multi-tenant or server product.

- **The `odoo-dev` group is a privilege.** A member can start, stop and run arbitrary commands as every run-as user whose agent is unlocked, and read what those users can read (Odoo configs with database passwords, filestores, backups). Add only people who already have that access, and only run-as users that exist for Odoo development.
- **Agents.** One agent per run-as user, started through `sudo` (`systemd-run --uid=<user>`), listening on `/run/odoo-dev-panel/<user>.sock` (mode `0660`, group `odoo-dev`). The agent refuses peers that are not in `odoo-dev`. It runs only absolute command paths.
- **sudo use.** The app calls sudo only for these, always showing the command or script first: starting an agent, adding a run-as user or yourself to `odoo-dev`, the generated provision script, and the generated `chown`/`chmod` script that applies the standard config permissions. The password goes to `sudo` through an askpass helper; the app never stores it.
- **Config permissions.** Recommended: config owned by the developer, group of the run-as user, mode `0640`, in a setgid folder used only by that installation. A per-installation group keeps one Odoo version from reading another version's passwords (all run-as users are in `odoo-dev`). Doctor reports configs that differ.
- **Config editor.** Passwords are masked until you press Reveal; a masked value keeps the current one on save. Backups of a config are written with mode `0600`.
- **Secrets.** Database and admin passwords are read from Odoo configs when needed and passed to PostgreSQL tools through the `PGPASSWORD` environment variable only. They never go into command lines, logs, receipts, plans or the registry. Doctor warns about configs with passwords that every local user can read.
- **Destructive actions.** Every mutating action has a read-only plan first. Dropping a database needs the typed name and moves the filestore to a `.trash-*` folder instead of deleting it. Restores go only into new database names. Neutralization recipes run only on the clone of the same job or on a database you name.
- **No network service.** Nothing listens on TCP. The app opens only `http://localhost:<port>` links of running instances.

## Out of scope

- Hardening Odoo itself, PostgreSQL or the run-as users' environments.
- Machines where untrusted users are in `odoo-dev` or have sudo.
