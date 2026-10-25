"""Credentials remain runtime-only and bound to the declared public SSH pin."""

import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

import rpi_secrets


class RpiSecretsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.key = self.root / "initrd"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.key)], check=True)
        self.password = self.root / "password"
        self.password.write_bytes(b"long-enough-fixture-password")
        self.password.chmod(0o600)
        self.target = {"security": {"mode": "luks", "initrdHostKey": self.key.with_suffix('.pub').read_text().strip()}}

    def test_private_key_must_match_the_pinned_public_key_and_have_private_permissions(self):
        password, key = rpi_secrets.credentials(self.target, self.password, self.key)
        self.assertEqual(password, self.password.read_bytes())
        self.assertEqual(key, self.key.read_bytes())
        self.target["security"]["initrdHostKey"] = "ssh-ed25519 DIFFERENT"
        with self.assertRaisesRegex(ValueError, "does not match"):
            rpi_secrets.credentials(self.target, self.password, self.key)
        self.key.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "private regular file"):
            rpi_secrets.credentials(self.target, self.password, self.key)

    def test_transport_uses_stdin_and_cleans_up_after_installer_failure(self):
        values = (b"fixture password", b"fixture private key")
        ssh = Mock()
        with self.assertRaisesRegex(RuntimeError, "installer failed"):
            with rpi_secrets.remote(ssh, values):
                raise RuntimeError("installer failed")
        calls = ssh.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertIn("mkdir /run/nixberry", calls[0].args[0])
        self.assertIn("rmdir -- /run/nixberry", calls[-1].args[0])
        payload = calls[1].kwargs["input_bytes"]
        with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
            self.assertEqual(archive.getnames(), list(rpi_secrets.FILES))
            for member, data in zip(archive.getmembers(), values, strict=True):
                self.assertEqual(member.mode, 0o600)
                self.assertEqual(archive.extractfile(member).read(), data)
        for call in calls:
            for value in values:
                self.assertNotIn(value.decode(), call.args[0])

    def test_does_not_delete_an_existing_runtime_directory(self):
        ssh = Mock(side_effect=RuntimeError("already exists"))
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            with rpi_secrets.remote(ssh, (b"password", b"key")):
                self.fail("Must not start installation")
        self.assertEqual(ssh.call_count, 1)
