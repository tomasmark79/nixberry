"""Runtime-only credentials for a verified physical RPi recovery installer."""

from contextlib import contextmanager
import io
import os
from pathlib import Path
import stat
import subprocess
import tarfile

from messages import _
from security import read_password, secret_directory


FILES = ("luks-password", "ssh_host_ed25519_key")


def credentials(target, key_file=None, initrd_key_file=None):
    if not initrd_key_file or not target.get("security", {}).get("initrdHostKey"):
        raise ValueError(_("Encrypted RPi installation requires --initrd-key-file and a trusted security.initrdHostKey pin."))
    path = Path(initrd_key_file).expanduser()
    recipe = os.environ.get("NIXBERRY_RECIPE")
    for source in (path, Path(key_file).expanduser() if key_file else None):
        if source is None:
            continue
        resolved = source.resolve()
        if str(resolved).startswith("/nix/store/") or (recipe and (
                resolved == Path(recipe).resolve() or Path(recipe).resolve() in resolved.parents)):
            raise ValueError(_("Keep RPi installation secrets outside the recipe and Nix store."))
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        state = os.fstat(stream.fileno())
        if not stat.S_ISREG(state.st_mode) or state.st_uid != os.getuid() or state.st_mode & 0o077:
            raise ValueError(_("The private initrd SSH key must be a private regular file owned by the current user."))
        key = stream.read(16385)
    if len(key) > 16384:
        raise ValueError(_("The private initrd SSH key is too large."))
    with secret_directory() as temporary:
        private = temporary / "key"
        private.write_bytes(key)
        private.chmod(0o600)
        public = subprocess.run(["ssh-keygen", "-y", "-P", "", "-f", str(private)],
                                capture_output=True, check=False)
    expected = target["security"]["initrdHostKey"].split()[:2]
    if public.returncode or public.stdout.decode().split()[:2] != expected:
        raise ValueError(_("The private initrd SSH key does not match the trusted RPi public key."))
    return read_password(key_file), key


def archive(values):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as stream:
        for name, data in zip(FILES, values, strict=True):
            member = tarfile.TarInfo(name)
            member.mode = 0o600
            member.size = len(data)
            stream.addfile(member, io.BytesIO(data))
    return output.getvalue()


@contextmanager
def remote(ssh, values):
    ssh('set -eu; test "$(stat -f -c %T /run)" = tmpfs; umask 077; mkdir /run/nixberry')
    try:
        ssh('umask 077; tar --no-same-owner --no-same-permissions -xmf - -C /run/nixberry',
            input_bytes=archive(values))
        yield
    finally:
        ssh('rm -f -- /run/nixberry/luks-password /run/nixberry/ssh_host_ed25519_key; rmdir -- /run/nixberry')


@contextmanager
def local(values):
    directory = Path("/run/nixberry")
    if subprocess.check_output(["stat", "-f", "-c", "%T", "/run"], text=True).strip() != "tmpfs":
        raise RuntimeError(_("RPi installation secrets require /run on tmpfs."))
    directory.mkdir(mode=0o700)
    try:
        for name, data in zip(FILES, values, strict=True):
            descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
        yield
    finally:
        for name in FILES:
            (directory / name).unlink(missing_ok=True)
        directory.rmdir()
