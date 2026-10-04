#!/usr/bin/env bash
# Headless acceptance run of Spike 0 through the odp CLI.
# Covers points 4, 5 (agent side), 9 and 10 of the plan, plus the access check.
# Usage:  spike/acceptance.sh [user]          (default user: odoo99)
# Needs:  setup-dev-host.sh done (or the .deb installed), and for a source tree: dev-sync.sh + dev-env.sh.
set -euo pipefail

USER_UNDER_TEST=${1:-odoo99}
ODP=${ODP_EXE:-/usr/lib/odoo-dev-panel/bin/odp}
SPIKE=${ODP_SPIKE_DIR:-/usr/lib/odoo-dev-panel/spike}
PY=${ODP_PYTHON:-/usr/lib/odoo-dev-panel/python/bin/python3.12}
SOCK=/run/odoo-dev-panel/$USER_UNDER_TEST.sock

pass() { printf '  \033[32mPASS\033[0m %s\n' "$*"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$*"; exit 1; }
step() { printf '\n== %s\n' "$*"; }
json() { "$PY" -c "import json,sys; print(json.load(sys.stdin)$1)"; }

step "Agent start (one sudo prompt expected)"
"$ODP" agent start -u "$USER_UNDER_TEST"
"$ODP" agent status -u "$USER_UNDER_TEST" | grep -q running && pass "agent running" || fail "agent not running"
[ "$(stat -c %a "$SOCK")" = 660 ] && pass "socket mode 0660" || fail "socket mode $(stat -c %a "$SOCK")"

step "Five start/stop cycles without sudo"
sudo -k  # drop cached credentials: any further sudo use would now prompt
for i in 1 2 3 4 5; do
    id=$("$ODP" --json run -u "$USER_UNDER_TEST" -- "$PY" "$SPIKE/fake_odoo.py" --workers 2 | json "['id']")
    sleep 1
    "$ODP" --json stop -u "$USER_UNDER_TEST" "$id" | json "['state']" | grep -q exited || fail "cycle $i"
done
pass "5 cycles, no prompt"

step "Session survives agent crash"
session=$("$ODP" --json run -u "$USER_UNDER_TEST" -- "$PY" "$SPIKE/fake_odoo.py" --workers 2)
id=$(echo "$session" | json "['id']")
pid=$(echo "$session" | json "['pid']")
sleep 2
"$ODP" logs -u "$USER_UNDER_TEST" "$id" | grep -q "Odoo version" && pass "log readable through agent" || fail "no log output"
agent_pid=$("$ODP" --json agent status -u "$USER_UNDER_TEST" | json "[0]['info']['pid']")
echo "  killing agent pid $agent_pid with SIGKILL (sudo prompt possible)"
sudo -u "$USER_UNDER_TEST" kill -9 "$agent_pid"
sleep 2
ps -o pid= -p "$pid" >/dev/null && pass "process $pid alive after agent kill -9" || fail "process died with agent"

step "Agent restart re-adopts the session"
"$ODP" agent start -u "$USER_UNDER_TEST"
"$ODP" --json ps -u "$USER_UNDER_TEST" | "$PY" -c "
import json, sys
s = {x['id']: x for x in json.load(sys.stdin)}['$id']
assert s['state'] == 'running' and s['adopted'], s
" && pass "session re-adopted" || fail "session not re-adopted"

step "Stop kills the process group, nothing else"
pgid=$(ps -o pgid= -p "$pid" | tr -d ' ')
"$ODP" stop -u "$USER_UNDER_TEST" "$id"
sleep 1
if pgrep -g "$pgid" >/dev/null; then fail "processes left in group $pgid"; else pass "group $pgid empty"; fi

step "Graceful agent stop leaves sessions running"
session=$("$ODP" --json run -u "$USER_UNDER_TEST" -- "$PY" "$SPIKE/fake_odoo.py")
id=$(echo "$session" | json "['id']")
pid=$(echo "$session" | json "['pid']")
"$ODP" agent stop -u "$USER_UNDER_TEST"
sleep 1
ps -o pid= -p "$pid" >/dev/null && pass "session survives 'odp agent stop'" || fail "session died"
"$ODP" agent start -u "$USER_UNDER_TEST"
"$ODP" stop -u "$USER_UNDER_TEST" "$id" >/dev/null && pass "stopped after re-adoption"

step "Access control"
if sudo -u nobody "$PY" -c "import socket; s=socket.socket(socket.AF_UNIX); s.connect('$SOCK')" 2>/dev/null; then
    fail "user nobody reached the agent socket"
else
    pass "user nobody cannot reach the agent socket"
fi

printf '\nAll headless checks passed.\n'
