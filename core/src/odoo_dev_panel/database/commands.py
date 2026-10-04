"""Command construction for PostgreSQL tools. Pure functions. The password travels only in ``Conn.env()``."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Conn:
    host: str | None  # None: the local Unix socket (libpq default)
    port: str
    user: str
    password: str | None = None

    def env(self) -> dict[str, str]:
        return {"PGPASSWORD": self.password} if self.password else {}

    def args(self) -> list[str]:
        return [*(["-h", self.host] if self.host else []), "-p", self.port, "-U", self.user]

    def needs_os_user(self, me: str) -> bool:
        """A Unix-socket connection without a password authenticates by OS user (peer): only the role's own OS
        user can open it, so queries must run through that user's agent."""
        return (not self.host or self.host.startswith("/")) and not self.password and self.user != me

    def public(self) -> dict:
        return {"host": self.host, "port": self.port, "user": self.user}


def default_host(password: str | None) -> str | None:
    """Odoo without db_host connects over the Unix socket (peer auth on Debian/Ubuntu), which only works as the
    role's own OS user. With a password, TCP localhost works for every caller, so it is used instead."""
    return "localhost" if password else None


def conn_from_options(options: dict[str, str], default_user: str | None = None) -> Conn | None:
    """From the [options] of an Odoo config, with Odoo's ``False`` meaning unset. Without db_user Odoo connects as
    its OS user, which is ``default_user`` (the run-as user). None when neither is known."""
    def get(key: str, default: str | None) -> str | None:
        value = options.get(key)
        return default if value in (None, "", "False") else value

    user = get("db_user", default_user)
    if not user:
        return None
    password = get("db_password", None)
    return Conn(get("db_host", default_host(password)), get("db_port", "5432"), user, password)


def dump_argv(c: Conn, database: str, file: str) -> list[str]:
    return ["pg_dump", *c.args(), "-Fc", "-f", file, "-d", database]


def restore_argv(c: Conn, database: str, file: str) -> list[str]:
    return ["pg_restore", *c.args(), "--no-owner", "--no-acl", "--exit-on-error", "-d", database, file]


# Read the dump's table of contents back; print only the entry count (the full list is thousands of lines).
LIST_DUMP_SCRIPT = 'set -o pipefail\nn=$(pg_restore --list -- "$1" | grep -vc "^;")\necho "$n entries in the dump"\n'


def createdb_argv(c: Conn, database: str) -> list[str]:
    return ["createdb", *c.args(), "-T", "template0", "-E", "UTF8", "--", database]


def dropdb_argv(c: Conn, database: str, if_exists: bool = False) -> list[str]:
    return ["dropdb", *c.args(), *(["--if-exists"] if if_exists else []), "--", database]


def psql_argv(c: Conn, database: str, *extra: str) -> list[str]:
    return ["psql", *c.args(), "-d", database, "-X", "-v", "ON_ERROR_STOP=1", *extra]


def verify_argv(c: Conn, database: str) -> list[str]:
    return psql_argv(c, database, "-A", "-t", "-c", "SELECT count(*) FROM ir_module_module")


ACTIVITY_ALL_SQL = ("SELECT datname, count(*) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND datname IS NOT NULL "
                    "GROUP BY datname")
# Neutralize through an agent: the recipe text arrives in $ODP_SQL (the agent cannot read the dev user's temp files).
PSQL_STDIN_SCRIPT = 'set -o pipefail\nprintf "%s\\n" "$ODP_SQL" | "$@"\n'


def activity_sql(database: str) -> str:
    """Sessions on ``database``. ``database`` is a validated name, never user text."""
    return f"SELECT count(*) FROM pg_stat_activity WHERE datname = '{database}' AND pid <> pg_backend_pid()"


# Scripts take every path and name as a positional argument ($1...), never pasted into shell text.
# Clone: dump of $2 piped into a restore into $3. Needs bash for pipefail.
# An empty $1 means the Unix socket: no -h at all.
CLONE_SCRIPT = ('set -o pipefail\npg_dump ${1:+-h "$1"} -p "$2" -U "$3" -Fc -d "$4" | '
                'pg_restore ${1:+-h "$1"} -p "$2" -U "$3" --no-owner --no-acl --exit-on-error -d "$5"\n')
# Make $2 (must not exist), then copy $1 into it. Prints ODP:created once $2 is ours, so a rollback removes only that.
COPY_TREE_SCRIPT = ('set -e\nmkdir -- "$2" || exit 3\necho ODP:created\ncp -a --reflink=auto -T -- "$1" "$2"\n')
# Backup: tar of the filestore $1 into $2.
TAR_SCRIPT = 'set -e\ntar -C "$1" -cf "$2" .\n'
# Restore: $1 backup folder, $2 new filestore folder. Nothing to do without filestore.tar.
UNTAR_SCRIPT = ('set -e\n[ -f "$1/filestore.tar" ] || { echo ODP:no-filestore; exit 0; }\n'
                'mkdir -- "$2" || exit 3\necho ODP:created\ntar -C "$2" -xf "$1/filestore.tar"\n')
SUMS_SCRIPT = 'set -e\ncd -- "$1"\nsha256sum -- * > SHA256SUMS.new\nmv SHA256SUMS.new SHA256SUMS\ncat SHA256SUMS\n'
CHECK_SUMS_SCRIPT = 'set -e\ncd -- "$1"\nsha256sum -c SHA256SUMS\n'
# Move $1 to $2 (a name that does not exist); undo with the reverse call.
MOVE_SCRIPT = 'set -e\n[ ! -e "$2" ]\nmv -T -- "$1" "$2"\n'
# Snapshots of database $2 in folder $1: keep the newest $3, remove the older ones. Only folders named
# <db>-YYYYMMDD-HHMMSS whose manifest says snapshot are touched, so a manual backup is never removed.
PRUNE_SCRIPT = (
    'set -e\ncd -- "$1"\nn=0\n'
    'for d in $(printf "%s\\n" "$2"-* | grep -E "^$2-[0-9]{8}-[0-9]{6}\\$" | sort -r || true); do\n'
    '  grep -q \'"snapshot": true\' "$d/manifest.json" 2>/dev/null || continue\n'
    '  n=$((n + 1))\n  [ "$n" -le "$3" ] && continue\n'
    '  rm -rf -- "$d"\n  echo "ODP:pruned $d"\ndone\n'
)
# One line per snapshot folder in $1: ODP:snap<TAB>name<TAB>bytes, then its manifest on one line.
LIST_SNAPSHOTS_SCRIPT = (
    'cd -- "$1" 2>/dev/null || exit 0\n'
    'for d in */; do d=${d%/}\n'
    '  [ -f "$d/manifest.json" ] || continue\n'
    '  printf "ODP:snap\\t%s\\t%s\\n" "$d" "$(du -sb -- "$d" | cut -f1)"\n'
    '  tr -d "\\n" < "$d/manifest.json"; echo\ndone\nexit 0\n'
)
# Remove the snapshot folder $1, only when its manifest says it is one.
FORGET_SCRIPT = 'set -e\ngrep -q \'"snapshot": true\' "$1/manifest.json"\nrm -rf -- "$1"\n'


def rename_sql(old: str, new: str) -> str:
    """Both names are validated (``paths.name_error``), never user text."""
    return f'ALTER DATABASE "{old}" RENAME TO "{new}"'
