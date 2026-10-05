"""make rotate-secrets, ported from NoAlibi and Letterbolt. Fault injection never uses the
checkout's secrets.

The integration test also exercises psql's password prompt and TCP authentication
against a disposable real PostgreSQL cluster when host PostgreSQL is installed.
"""

import os
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "rotate-secrets.sh"
KEYS = ("POSTGRES_PASSWORD", "SECRET_KEY", "ADMIN_DASHBOARD_TOKEN")
EXTERNAL = """# External credentials must remain exactly as written.
SMTP_PASSWORD='external\\npassword$literal'
OPENAI_API_KEY=sk-external-provider-key
ADMIN_DASHBOARD_PATH=/private-existing-console
"""
ENV = """POSTGRES_USER=rotation_test
POSTGRES_DB=postgres
POSTGRES_PASSWORD='old-database-password'
SECRET_KEY=old-secret-key
SECRET_KEY_PREVIOUS=
ADMIN_DASHBOARD_TOKEN=old-admin-token
REFRESH_TOKEN_EXPIRE_DAYS=30
TEST_DATABASE_URL=
""" + EXTERNAL

# Fake Docker/Make executables model operator failures; they also assert that
# neither old nor newly generated secrets ever appear in command arguments.
ADAPTER = r"""#!/usr/bin/env python3
import os, pathlib, re, subprocess, sys
root = pathlib.Path(os.environ['ROTATE_TEST_ROOT'])
args = sys.argv[1:]
kind = pathlib.Path(sys.argv[0]).name
source = (root/'.env').read_text()
known = ['old-database-password', 'old-secret-key', 'old-admin-token']
for line in source.splitlines():
    if line.startswith(('POSTGRES_PASSWORD=', 'SECRET_KEY=', 'SECRET_KEY_PREVIOUS=', 'ADMIN_DASHBOARD_TOKEN=')):
        known.append(line.split('=',1)[1].strip("'\""))
assert not any(secret and secret in ' '.join(args) for secret in known)
assert not re.search(r'\b[a-f0-9]{64}\b', ' '.join(args))
def event(name):
    with (root/'events').open('a') as f: f.write(name+'\n')
fault = os.environ.get('ROTATE_TEST_FAULT', '')
if kind == 'make':
    assert args == ['-C', str(root), 'up'], args
    event('up')
    assert (root/'.env').stat().st_mode & 0o777 == 0o600
    if fault == 'up': sys.exit(1)
    sys.exit(0)
if kind == 'mv':
    event('publish-failed')
    sys.exit(1)
if 'inspect' in args:
    event('health')
    print('unhealthy' if fault == 'health' else 'healthy')
    sys.exit(0)
if 'ps' in args:
    print('test-api-container')
    sys.exit(0)
if 'config' in args:
    event('config')
    sys.exit(1 if fault == 'config' else 0)
assert 'exec' in args and '-T' in args and '--user' in args
command = args[args.index('--user')+3:]
assert command[0] in ('psql', 'sh'), command
if command[0] == 'sh':
    payload = sys.stdin.read()
    password = payload.rstrip('\n')
    operation = 'verify-old' if password == 'old-database-password' else 'verify-new'
elif '\password' in ' '.join(command):
    payload = sys.stdin.read()
    passwords = payload.splitlines()
    assert len(passwords) == 2 and passwords[0] == passwords[1]
    password = passwords[0]
    operation = 'restore' if password == 'old-database-password' else 'change'
else:
    payload = ''
    operation = 'socket'
event(operation)
if fault == operation or (fault == 'rollback' and operation in ('verify-new', 'restore')):
    sys.exit(1)
if os.environ.get('ROTATE_TEST_REAL_PG'):
    sys.exit(subprocess.run(command, input=payload, text=True).returncode)
state = root/'database-password'
if operation in ('change', 'restore'): state.write_text(password)
if operation == 'change' and fault == 'change-ambiguous': sys.exit(1)
if operation.startswith('verify'):
    sys.exit(0 if password == state.read_text() else 1)
"""


class RotationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="scangenai-rotation-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ("scripts", "bin"):
            (self.root / directory).mkdir()
        shutil.copy2(SCRIPT, self.root / "scripts/rotate-secrets.sh")
        guard = self.root / "scripts/check-env.sh"
        guard.write_text("#!/bin/sh\nexit 0\n")
        guard.chmod(0o700)
        self.envfile = self.root / ".env"
        self.envfile.write_text(ENV)
        self.envfile.chmod(0o600)
        (self.root / "docker-compose.yml").touch()
        (self.root / "database-password").write_text("old-database-password")
        for command in ("docker", "make"):
            self.adapter(command)
        self.process_env = dict(
            os.environ,
            PATH=str(self.root / "bin") + os.pathsep + os.environ["PATH"],
            ROTATE_TEST_ROOT=str(self.root),
        )
        self.process_env.pop("ROTATE_TEST_FAULT", None)
        self.process_env.pop("ROTATE_TEST_REAL_PG", None)

    def adapter(self, command):
        path = self.root / "bin" / command
        path.write_text(ADAPTER)
        path.chmod(0o700)

    def run_rotation(self, fault="", *args):
        result = subprocess.run(
            ["bash", str(self.root / "scripts/rotate-secrets.sh"), *args],
            env=dict(self.process_env, ROTATE_TEST_FAULT=fault),
            text=True,
            capture_output=True,
            timeout=30,
        )
        for secret in (
            "old-database-password",
            "old-secret-key",
            "old-admin-token",
            "external\\npassword",
        ):
            self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertNotRegex(result.stdout + result.stderr, r"\b[a-f0-9]{64}\b")
        return result

    def events(self):
        path = self.root / "events"
        return path.read_text().splitlines() if path.exists() else []

    def values(self):
        return dict(
            line.split("=", 1)
            for line in self.envfile.read_text().splitlines()
            if "=" in line
        )

    def assert_unchanged(self):
        self.assertEqual(self.envfile.read_text(), ENV)
        self.assertEqual(
            (self.root / "database-password").read_text(), "old-database-password"
        )
        self.assertNotIn("up", self.events())
        self.assertFalse((self.root / ".rotate-secrets").exists())

    def test_rotates_only_owned_credentials_before_make_up(self):
        result = self.run_rotation()
        self.assertEqual(result.returncode, 0, result.stderr)
        values = self.values()
        for key in KEYS:
            self.assertRegex(values[key], r"^[a-f0-9]{64}$")
        self.assertEqual(len({values[key] for key in KEYS}), 3)
        # The old signing key keeps verifying existing sessions.
        self.assertEqual(values["SECRET_KEY_PREVIOUS"], "old-secret-key")
        self.assertTrue(self.envfile.read_text().endswith(EXTERNAL))
        self.assertEqual(self.envfile.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            values["POSTGRES_PASSWORD"], (self.root / "database-password").read_text()
        )
        self.assertEqual(
            self.events(),
            ["config", "socket", "verify-old", "change", "verify-new", "up", "health"],
        )
        self.assertFalse((self.root / ".rotate-secrets").exists())

    def test_preserves_disabled_admin_console(self):
        self.envfile.write_text(
            ENV.replace(
                "ADMIN_DASHBOARD_TOKEN=old-admin-token",
                "ADMIN_DASHBOARD_TOKEN='' # disabled",
            )
        )
        result = self.run_rotation()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ADMIN_DASHBOARD_TOKEN='' # disabled", self.envfile.read_text())

    def test_preflight_failures_leave_credentials_untouched(self):
        for fault in ("config", "socket", "verify-old"):
            with self.subTest(fault=fault):
                self.assertNotEqual(self.run_rotation(fault).returncode, 0)
                self.assert_unchanged()

    def test_database_failures_restore_old_password(self):
        for fault in ("change", "change-ambiguous", "verify-new"):
            with self.subTest(fault=fault):
                result = self.run_rotation(fault)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("restored the original", result.stderr)
                self.assert_unchanged()

    def test_failed_env_publication_rolls_back_database(self):
        self.adapter("mv")
        self.assertNotEqual(self.run_rotation().returncode, 0)
        self.assert_unchanged()

    def test_failed_startup_keeps_new_env_and_database_in_sync(self):
        result = self.run_rotation("up")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            self.values()["POSTGRES_PASSWORD"],
            (self.root / "database-password").read_text(),
        )
        self.assertNotEqual(self.values()["SECRET_KEY"], "old-secret-key")
        self.assertIn("then run make up", result.stderr)
        self.assertNotIn("restore", self.events())
        self.assertFalse((self.root / ".rotate-secrets").exists())

    def test_failed_rollback_retains_private_recovery_state_and_lock(self):
        result = self.run_rotation("rollback")
        self.assertNotEqual(result.returncode, 0)
        lock = self.root / ".rotate-secrets"
        self.assertTrue((lock / "pending").exists())
        self.assertEqual(lock.stat().st_mode & 0o777, 0o700)
        self.assertEqual((lock / "pending").stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.envfile.read_text(), ENV)
        self.assertNotEqual(self.run_rotation().returncode, 0)
        self.assertNotIn("up", self.events())

    def test_unhealthy_api_reports_failure_without_reverting_credentials(self):
        result = self.run_rotation("health")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed its health check", result.stderr)
        self.assertEqual(
            self.values()["POSTGRES_PASSWORD"],
            (self.root / "database-password").read_text(),
        )
        self.assertNotIn("restore", self.events())

    def test_refuses_to_drop_a_key_that_can_still_verify_sessions(self):
        state = self.root / ".secret-rotation"
        state.mkdir()
        (state / "last-rotated").write_text(str(int(__import__("time").time()) - 86400))
        result = self.run_rotation()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("previous rotation was too recent", result.stderr)
        self.assertIn("29 more day", result.stderr)
        self.assertEqual(self.envfile.read_text(), ENV)
        self.assertEqual(self.events(), [])

        result = self.run_rotation("", "--force")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_records_when_it_rotated(self):
        self.assertEqual(self.run_rotation().returncode, 0)
        stamp = self.root / ".secret-rotation" / "last-rotated"
        self.assertRegex(stamp.read_text().strip(), r"^\d+$")
        self.assertEqual(stamp.parent.stat().st_mode & 0o777, 0o700)

    def test_refuses_duplicate_or_dynamic_values_without_executing_them(self):
        for content in (
            ENV + "SECRET_KEY=duplicate\n",
            ENV.replace("old-admin-token", "$(touch should-not-exist)"),
        ):
            with self.subTest(content=content):
                self.envfile.write_text(content)
                self.assertNotEqual(self.run_rotation().returncode, 0)
                self.assertEqual(self.envfile.read_text(), content)
                self.assertEqual(self.events(), [])

    def test_literal_dollar_passwords_reach_authentication(self):
        for value in ("old$9-password", '"old$9-password"', "'old$NAME-password'"):
            with self.subTest(value=value):
                shutil.rmtree(self.root / ".secret-rotation", ignore_errors=True)
                password = value.strip("\"'")
                content = ENV.replace("'old-database-password'", value)
                self.envfile.write_text(content)
                (self.root / "database-password").write_text(password)
                result = self.run_rotation()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn(password, result.stdout + result.stderr)

    def test_interpolated_passwords_fail_before_docker(self):
        for value in ("old$NAME", "old${NAME}", "old$$escaped", "$(touch sentinel)"):
            with self.subTest(value=value):
                content = ENV.replace("'old-database-password'", value)
                self.envfile.write_text(content)
                self.assertNotEqual(self.run_rotation().returncode, 0)
                self.assertEqual(self.envfile.read_text(), content)
                self.assertEqual(self.events(), [])

    def test_openssl_failure_leaves_env_and_database_unchanged(self):
        path = self.root / "bin/openssl"
        path.write_text("#!/bin/sh\nexit 1\n")
        path.chmod(0o700)
        self.assertNotEqual(self.run_rotation().returncode, 0)
        self.assert_unchanged()

    @unittest.skipUnless(
        all(shutil.which(c) for c in ("initdb", "pg_ctl", "psql"))
        and os.geteuid() != 0,
        "requires host PostgreSQL tools and a non-root user",
    )
    def test_real_postgres_password_rotation_preserves_data(self):
        # Keep the socket path short enough for macOS's Unix socket limit.
        pgtemp = tempfile.TemporaryDirectory(prefix="napg-", dir="/tmp")
        self.addCleanup(pgtemp.cleanup)
        pgroot = Path(pgtemp.name)
        pwfile = pgroot / "password"
        pwfile.write_text("old-database-password\n")
        pwfile.chmod(0o600)
        pgenv = dict(
            os.environ,
            LC_ALL="C",
            PGHOST=str(pgroot),
            PGDATABASE="postgres",
            PGUSER="rotation_test",
        )
        pgenv.pop("PGPASSWORD", None)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            pgenv["PGPORT"] = str(listener.getsockname()[1])

        def pg(command, **kwargs):
            return subprocess.run(
                command, env=pgenv, capture_output=True, text=True, check=True, **kwargs
            )

        pg(
            [
                "initdb",
                "-D",
                str(pgroot / "data"),
                "-U",
                "rotation_test",
                "--auth-local=trust",
                "--auth-host=scram-sha-256",
                "--pwfile=" + str(pwfile),
            ]
        )
        pg(
            [
                "pg_ctl",
                "-D",
                str(pgroot / "data"),
                "-l",
                str(pgroot / "log"),
                "-w",
                "start",
                "-o",
                "-h 127.0.0.1 -k " + str(pgroot) + " -p " + pgenv["PGPORT"],
            ]
        )
        self.addCleanup(
            lambda: pg(
                ["pg_ctl", "-D", str(pgroot / "data"), "-m", "fast", "-w", "stop"]
            )
        )
        pg(
            [
                "psql",
                "-X",
                "-c",
                "CREATE TABLE rotation_sentinel (value text); INSERT INTO rotation_sentinel VALUES ('kept');",
            ]
        )
        self.process_env.update(
            {k: pgenv[k] for k in ("PGHOST", "PGPORT", "PGUSER", "PGDATABASE")}
        )
        self.process_env["ROTATE_TEST_REAL_PG"] = "1"
        # The test server enforces scram on 127.0.0.1, standing in for the
        # container address the script uses in production.
        self.process_env["ROTATE_VERIFY_HOST"] = "127.0.0.1"
        result = self.run_rotation()
        self.assertEqual(result.returncode, 0, result.stderr)
        pgenv.update(PGHOST="127.0.0.1", PGPASSWORD=self.values()["POSTGRES_PASSWORD"])
        self.assertEqual(
            pg(
                ["psql", "-X", "-w", "-Atc", "SELECT value FROM rotation_sentinel"]
            ).stdout.strip(),
            "kept",
        )
        pgenv["PGPASSWORD"] = "old-database-password"
        rejected = subprocess.run(
            ["psql", "-X", "-w", "-Atc", "SELECT 1"], env=pgenv, capture_output=True
        )
        self.assertNotEqual(rejected.returncode, 0)


if __name__ == "__main__":
    unittest.main()
