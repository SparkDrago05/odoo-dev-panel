import base64
import hashlib
import hmac
import unittest
from datetime import datetime, timezone

from odoo_dev_panel.provision import plan, preflight, scram
from odoo_dev_panel.provision.spec import CustomRepo, ProvisionSpec, SpecError


def spec(**kw):
    kw.setdefault("version", 17)
    kw.setdefault("dev_user", "alice")
    return ProvisionSpec(**kw)


class FakeFacts(preflight.SystemFacts):
    def __init__(self, **over):
        self.over = {"paths": {"/opt/odoo-dev-panel/python"}, "users": set(), "groups": {"odoo-dev"}, "member": True, "commands": {"git", "sudo", "wkhtmltopdf", "node", "rtlcss"},
                     "free": 50 * 1024**3, "ports": {5432}, "files": set(), "current": "alice"}
        self.over.update(over)

    def path_exists(self, path): return path in self.over["paths"]
    def user_exists(self, name): return name in self.over["users"]
    def group_exists(self, name): return name in self.over["groups"]
    def in_group(self, group): return self.over["member"]
    def which(self, command): return "/usr/bin/" + command if command in self.over["commands"] else None
    def free_bytes(self, path): return self.over["free"]
    def port_open(self, host, port): return port in self.over["ports"]
    def file_readable(self, path): return path in self.over["files"]
    def current_user(self): return self.over["current"]


def status(checks):
    return {c.id: c.status for c in checks}


class ScramTest(unittest.TestCase):
    def test_rfc7677_vector(self):
        salt = base64.b64decode("W22ZaJ0SNY7soEsUEjb6gQ==")
        stored, server = scram.keys("pencil", salt, 4096)
        auth = ("n=user,r=rOprNGfwEbeRWgbNEkqO,"
                "r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096,"
                "c=biws,r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0").encode()
        salted = hashlib.pbkdf2_hmac("sha256", b"pencil", salt, 4096)
        client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
        signature = hmac.new(stored, auth, hashlib.sha256).digest()
        proof = bytes(a ^ b for a, b in zip(client_key, signature))
        self.assertEqual(base64.b64encode(proof).decode(), "dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ=")
        self.assertEqual(base64.b64encode(hmac.new(server, auth, hashlib.sha256).digest()).decode(),
                         "6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4=")

    def test_verifier_format(self):
        v = scram.verifier("secret")
        self.assertRegex(v, r"^SCRAM-SHA-256\$4096:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+$")
        self.assertNotIn("secret", v)
        self.assertNotEqual(v, scram.verifier("secret"), "salt must be random")


class SpecTest(unittest.TestCase):
    def test_defaults(self):
        s = spec(version=15)
        self.assertEqual((s.run_as, s.root, s.python, s.odoo_branch, s.config_dir),
                         ("odoo15", "/opt/odoo15", "3.10", "15.0", "/etc/odoo/odoo15"))
        self.assertEqual(spec(version=19).python, "3.12")

    def test_rejects_bad_input(self):
        for kw in ({"version": 14}, {"version": 20}, {"run_as": "Bad User"}, {"root": "relative"}, {"root": "/opt/a b"},
                   {"root": "/opt/x/"}, {"root": "/opt/../etc"}, {"root": "//opt/x"}, {"root": "/"}, {"python": "three"}, {"odoo_git": "ftp://x"}, {"odoo_git": "https://x/'; rm -rf /"},
                   {"odoo_branch": "-x"}, {"config_name": "../x"}, {"run_as": "alice"},
                   {"enterprise_git": "https://x/e.git", "enterprise_archive": "/tmp/e.zip"},
                   {"custom": [CustomRepo("https://x/a.git", "a"), CustomRepo("https://x/b.git", "a")]},
                   {"http_port": 80}, {"pg_host": "a b"}):
            with self.assertRaises(SpecError, msg=str(kw)):
                spec(**kw)

    def test_custom_repo_parse(self):
        self.assertEqual(CustomRepo.parse("https://lab.x/cms/core.git#staging-17"), CustomRepo("https://lab.x/cms/core.git", "core", "staging-17"))
        self.assertEqual(CustomRepo.parse("hr=git@lab.x:hr/core.git"), CustomRepo("git@lab.x:hr/core.git", "hr", None))


class PlanTest(unittest.TestCase):
    def test_conf(self):
        s = spec(enterprise_archive="/tmp/e.zip", custom=[CustomRepo("https://x/c.git", "c")], http_port=8070)
        sec = plan.Secrets("PGPASS", "ADMINPASS")
        text = plan.render_conf(s, sec, datetime(2026, 10, 3, tzinfo=timezone.utc))
        self.assertIn("db_user = odoo17", text)
        self.assertIn("db_password = PGPASS", text)
        self.assertIn("admin_passwd = ADMINPASS", text)
        self.assertIn("http_port = 8070", text)
        self.assertIn("addons_path = /opt/odoo17/odoo/addons,\n\t/opt/odoo17/enterprise,\n\t/opt/odoo17/custom/c\n", text)
        self.assertNotIn("db_name", text)
        self.assertNotIn("enterprise", plan.render_conf(spec(), sec))

    def test_root_script_has_no_plaintext(self):
        sec = plan.Secrets.generate()
        script = plan.render_root_script(spec(), sec)
        self.assertNotIn(sec.pg_password, script)
        self.assertNotIn(sec.admin_password, script)
        self.assertIn("SCRAM-SHA-256$4096:", script)
        for needle in ("set -euo pipefail", "useradd --system --user-group --create-home", 'usermod -aG odoo-dev "$RUN_AS"',
                       "install -d -m 2775", "chmod 2775", "apt-get install -y --no-install-recommends", "CREATE ROLE \"odoo17\" LOGIN CREATEDB", "ALTER ROLE \"odoo17\" LOGIN CREATEDB",
                       'runuser -u "$RUN_AS" --', "agent serve", '"state": "running"', "systemd-run --quiet --collect", "--property=KillMode=process"):
            self.assertIn(needle, script)

    def test_root_script_is_valid_bash(self):
        import subprocess
        script = plan.render_root_script(spec(), plan.Secrets.generate())
        result = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_clone_commands_are_quoted(self):
        cmd = plan.clone_command("https://x/y.git", "feat/a", "/opt/odoo17/odoo")
        self.assertEqual(cmd, "git clone --depth=1 --single-branch --no-tags --branch feat/a https://x/y.git /opt/odoo17/odoo")

    def test_steps(self):
        s = spec(enterprise_git="https://x/e.git", custom=[CustomRepo("https://x/c.git", "c")])
        steps = plan.build_steps(s, plan.Secrets.generate())
        ids = [x.id for x in steps]
        self.assertEqual(ids, ["root-script", "clone-odoo", "enterprise", "custom-c", "python", "venv", "pip", "config", "verify"])
        self.assertEqual([x.phase for x in steps], sorted(x.phase for x in steps))
        self.assertTrue(any("--branch 17.0 https://x/e.git" in c for c in steps[2].commands))
        self.assertNotIn("enterprise", [x.id for x in plan.build_steps(spec(), plan.Secrets.generate())])


class PreflightTest(unittest.TestCase):
    def test_all_ok(self):
        checks = preflight.run_preflight(spec(), FakeFacts())
        self.assertFalse(preflight.has_failures(checks), [c for c in checks if c.status != "ok"])

    def test_existing_installation_is_reused_with_warnings(self):
        s = spec()
        checks = preflight.run_preflight(s, FakeFacts(paths={s.root, "/opt/odoo-dev-panel/python"}, users={s.run_as}))
        res = status(checks)
        self.assertEqual((res["root-path"], res["run-as-user"]), ("warn", "warn"))
        self.assertFalse(preflight.has_failures(checks))

    def test_existing_readable_config_is_kept(self):
        s = spec()
        res = status(preflight.run_preflight(s, FakeFacts(paths={s.conf_path, "/opt/odoo-dev-panel/python"}, files={s.conf_path})))
        self.assertEqual(res["config-file"], "warn")

    def test_existing_unreadable_config_fails(self):
        s = spec()
        self.assertEqual(status(preflight.run_preflight(s, FakeFacts(paths={s.conf_path})))["config-file"], "fail")

    def test_missing_prerequisites(self):
        res = status(preflight.run_preflight(spec(enterprise_archive="/tmp/e.zip"),
                     FakeFacts(member=False, commands=set(), free=1024**3, ports=set(), groups=set())))
        for check in ("group", "group-member", "git", "sudo", "postgres", "disk", "enterprise-archive"):
            self.assertEqual(res[check], "fail", check)
        self.assertEqual((res["wkhtmltopdf"], res["node-rtlcss"]), ("warn", "warn"))

    def test_http_port_busy_is_warning(self):
        res = status(preflight.run_preflight(spec(http_port=8070), FakeFacts(ports={5432, 8070})))
        self.assertEqual(res["http-port"], "warn")


if __name__ == "__main__":
    unittest.main()
