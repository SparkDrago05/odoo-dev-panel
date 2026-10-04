import json
import subprocess
import unittest

from odoo_dev_panel.discover import docker


def row(name, image, env=(), cmd=None, entrypoint=None, mounts=(), ports=None, labels=None, running=True):
    return {
        "Id": f"{name:x<64}", "Name": f"/{name}", "Image": "sha256:abc",
        "Config": {"Image": image, "Env": list(env), "Cmd": cmd, "Entrypoint": entrypoint, "Labels": labels or {}},
        "State": {"Status": "running" if running else "exited", "Running": running, "StartedAt": "2026-10-04T10:00:00Z"},
        "Mounts": list(mounts), "NetworkSettings": {"Ports": ports or {}},
    }


def bind(src, dest, rw=True):
    return {"Type": "bind", "Source": src, "Destination": dest, "RW": rw}


def volume(name, dest):
    return {"Type": "volume", "Name": name, "Source": f"/var/lib/docker/volumes/{name}/_data", "Destination": dest, "RW": True}


def compose(project, service):
    return {"com.docker.compose.project": project, "com.docker.compose.service": service,
            "com.docker.compose.project.working_dir": f"/home/dev/{project}",
            "com.docker.compose.project.config_files": f"/home/dev/{project}/compose.yaml"}


OFFICIAL = row(
    "shop-web-1", "odoo:17.0",
    env=["HOST=db", "USER=odoo", "PASSWORD=hunter2", "ODOO_VERSION=17.0", "ODOO_RC=/etc/odoo/odoo.conf", "PATH=/usr/bin"],
    entrypoint=["/entrypoint.sh"], cmd=["odoo"],
    mounts=[bind("/home/dev/shop/config", "/etc/odoo"), bind("/home/dev/shop/addons", "/mnt/extra-addons"),
            volume("shop_odoo-web-data", "/var/lib/odoo")],
    ports={"8069/tcp": [{"HostIp": "0.0.0.0", "HostPort": "10017"}, {"HostIp": "::", "HostPort": "10017"}], "8072/tcp": None},
    labels=compose("shop", "web"),
)
DB = row("shop-db-1", "postgres:16", env=["POSTGRES_PASSWORD=hunter2", "POSTGRES_USER=odoo"], labels=compose("shop", "db"))
PLAIN = row("odoo16", "odoo:16", env=["ODOO_VERSION=16.0"], cmd=["odoo", "--config=/srv/odoo.conf"],
            mounts=[bind("/srv/odoo16", "/srv")], running=False)
CUSTOM = row("erp", "registry.local:5000/acme/odoo-custom:18.0-20260101", cmd=["/opt/odoo/odoo-bin", "-c", "/opt/conf/erp.conf"])
OTHER = row("other-8069", "acme/tenant:latest", env=["SERVER_HTTP_PORT=8069", "API_TOKEN=x"],
            ports={"8069/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8069"}]})


class Pure(unittest.TestCase):
    def test_identification(self):
        self.assertTrue(docker.is_odoo(OFFICIAL))
        self.assertTrue(docker.is_odoo(PLAIN))
        self.assertTrue(docker.is_odoo(CUSTOM))
        self.assertFalse(docker.is_odoo(OTHER))
        self.assertTrue(docker.is_odoo(row("x", "python:3.12", cmd=["/usr/bin/odoo-bin"])))
        self.assertFalse(docker.is_odoo(row("x", "python:3.12", cmd=["python", "odoo_tools.py"])))

    def test_official_compose(self):
        got = docker.odoo_containers([OTHER, DB, OFFICIAL])
        self.assertEqual([c["name"] for c in got], ["shop-web-1"])
        c = got[0]
        self.assertEqual(c["version"], "17.0")
        self.assertTrue(c["running"])
        self.assertEqual(c["compose"]["project"], "shop")
        self.assertEqual(c["compose"]["files"], ["/home/dev/shop/compose.yaml"])
        self.assertEqual(c["config"], {"container": "/etc/odoo/odoo.conf", "host": "/home/dev/shop/config/odoo.conf",
                                       "volume": None, "anonymous": False, "in_image": False})
        self.assertEqual(c["addons"][0]["host"], "/home/dev/shop/addons")
        self.assertEqual(c["data"], {"container": "/var/lib/odoo", "host": None, "volume": "shop_odoo-web-data", "anonymous": False, "in_image": False})
        self.assertEqual(c["db"], {"host": "db", "port": None, "user": "odoo", "container": "shop-db-1"})
        self.assertEqual([(p["host_port"], p["container"]) for p in c["ports"]], [(10017, "8069/tcp")])

    def test_secrets_never_kept(self):
        text = json.dumps(docker.odoo_containers([OFFICIAL, DB, OTHER, PLAIN, CUSTOM]))
        self.assertNotIn("hunter2", text)

    def test_plain_run_and_custom_image(self):
        plain, custom = (docker.odoo_containers([r])[0] for r in (PLAIN, CUSTOM))
        self.assertEqual(plain["version"], "16.0")
        self.assertFalse(plain["running"])
        self.assertIsNone(plain["compose"])
        self.assertEqual(plain["config"]["host"], "/srv/odoo16/odoo.conf")
        self.assertIsNone(plain["db"]["container"])
        self.assertEqual(custom["version"], "18.0")
        self.assertEqual(custom["config"], {"container": "/opt/conf/erp.conf", "host": None, "volume": None, "anonymous": False, "in_image": True})

    def test_host_path_picks_deepest_mount(self):
        mounts = [{"type": "bind", "source": "/h", "name": None, "destination": "/etc", "rw": True},
                  {"type": "bind", "source": "/h2", "name": None, "destination": "/etc/odoo", "rw": True}]
        self.assertEqual(docker.host_path("/etc/odoo/a.conf", mounts)["host"], "/h2/a.conf")
        self.assertEqual(docker.host_path("/etc/other", mounts)["host"], "/h/other")
        self.assertEqual(docker.host_path("/etc/odoo", mounts)["host"], "/h2")
        self.assertTrue(docker.host_path("/opt/x", mounts)["in_image"])
        self.assertIsNone(docker.host_path(None, mounts))
        anon = [{"type": "volume", "source": "/var/lib/docker/volumes/x", "name": "0eb17440a9cb" + "a" * 52,
                 "destination": "/var/lib/odoo", "rw": True}]
        got = docker.host_path("/var/lib/odoo", anon)
        self.assertEqual((got["volume"], got["anonymous"]), ("0eb17440a9cb", True))


def fake(responses):
    calls = []

    def run(argv):
        calls.append(argv)
        out = responses[argv[1]]
        if isinstance(out, Exception):
            raise out
        code, stdout, stderr = out
        return subprocess.CompletedProcess(argv, code, stdout, stderr)
    return run, calls


class Access(unittest.TestCase):
    def test_no_docker(self):
        got = docker.discover_docker(which=lambda _n: None)
        self.assertEqual((got["available"], got["error"]), (False, "docker is not installed"))

    def test_permission_denied(self):
        run, _ = fake({"ps": (1, "", "permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock")})
        got = docker.discover_docker(run, lambda _n: "/usr/bin/docker")
        self.assertIn("docker group", got["error"])
        self.assertFalse(got["available"])

    def test_daemon_down_and_timeout(self):
        run, _ = fake({"ps": (1, "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?")})
        self.assertEqual(docker.discover_docker(run, lambda _n: "x")["error"], "the Docker daemon is not running")
        run, _ = fake({"ps": subprocess.TimeoutExpired(["docker"], 10)})
        self.assertIn("did not answer", docker.discover_docker(run, lambda _n: "x")["error"])

    def test_no_containers_runs_no_inspect(self):
        run, calls = fake({"ps": (0, "\n", "")})
        got = docker.discover_docker(run, lambda _n: "x")
        self.assertEqual((got["containers"], got["error"], got["available"]), ([], None, True))
        self.assertEqual(len(calls), 1)

    def test_only_read_commands(self):
        run, calls = fake({"ps": (0, "a\nb\n", ""), "inspect": (0, json.dumps([OFFICIAL, DB]), "")})
        got = docker.discover_docker(run, lambda _n: "x")
        self.assertEqual([c["name"] for c in got["containers"]], ["shop-web-1"])
        self.assertEqual([c[1] for c in calls], ["ps", "inspect"])
        self.assertEqual(calls[1][2:], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
