"""Runs inside the test container: compare the discovery snapshot with the ground truth built by discover-setup.sh."""

import json
import sys


def fail(msg):
    print("  FAIL", msg)
    sys.exit(1)


snap = json.load(open("/tmp/discover.json"))
inst = {i["root"]: i for i in snap["installations"]}
if set(inst) != {"/opt/odoo17", "/home/dev/src/odoo", "/home/dev/old"}:
    fail(f"installations {sorted(inst)}")
a, p, b = inst["/opt/odoo17"], inst["/home/dev/src/odoo"], inst["/home/dev/old"]
if (a["version"], a["owner"], a["venv_ok"], a["pg_role"]) != ("17.0", "odoo17", True, "odoo17"):
    fail(f"nested {a}")
if (p["version"], p["venv"], p["venv_ok"]) != ("18.0", "/home/dev/src/odoo/.venv", True):
    fail(f"plain {p}")
if (b["version"], b["venv_ok"]) != ("15.0", False):
    fail(f"broken venv {b}")

conf = {c["path"]: c for c in snap["instances"]}
if conf["/etc/odoo/odoo17/shop.conf"]["installation"] != "/opt/odoo17":
    fail("shop.conf link")
if conf["/home/dev/src/odoo/odoo.conf"]["installation"] != "/home/dev/src/odoo":
    fail("plain conf link")
if conf["/etc/odoo/odoo13/old.conf"]["installation"] is not None:
    fail("orphan linked")
if "secret17" in json.dumps(snap):
    fail("secret leaked")

dbs = {d["name"]: d for d in next(e for e in snap["databases"] if e["installation"] == "/opt/odoo17")["databases"]}
if sorted(dbs) != ["blog", "shop"]:
    fail(f"databases {sorted(dbs)}")
if not dbs["shop"]["filestore_exists"] or dbs["blog"]["filestore_exists"]:
    fail("filestore flags")
if dbs["shop"]["filestore"] != "/opt/odoo17/.local/share/Odoo/filestore/shop":
    fail("filestore path")

if len(snap["processes"]) != 1:
    fail(f"processes {snap['processes']}")
proc = snap["processes"][0]
if (proc["user"], proc["instance"], proc["database"], proc["link"]) != ("odoo17", "/etc/odoo/odoo17/shop.conf", "shop", "config"):
    fail(f"process {proc}")
print("  discovery: ok")
