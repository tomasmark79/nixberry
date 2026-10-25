"""Runtime-only encrypted media preparation and strict early-boot SSH access."""

from contextlib import contextmanager
import getpass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

from messages import _, info


FORMATS = {"libvirt": "qcow2", "proxmox": "qcow2", "hyperv": "vhdx",
           "virtualbox": "vdi", "vmware": "vmdk"}


def policy(target):
    value = target.get("security", {})
    rpi = target.get("backend") == "rpi"
    fields = {"mode", "unlock", "port", "initrdHostKey", "recoveryHostKey"} if rpi else {"mode", "unlock", "port", "imageDirectory"}
    if not isinstance(value, dict) or set(value) - fields:
        raise ValueError(_("VM security contains unsupported fields; keep secrets outside declarations."))
    mode = value.get("mode", "none")
    if mode not in ("none", "luks", "confidential"):
        raise ValueError(_("Unknown VM security mode."))
    if mode == "confidential" and rpi:
        raise ValueError(_("Physical Raspberry Pi targets support LUKS, not confidential VM launch."))
    unlock = value.get("unlock", "attested" if mode == "confidential" else "manual")
    if unlock != ("attested" if mode == "confidential" else "manual"):
        raise ValueError(_("The unlock policy does not match the VM security mode."))
    port = value.get("port", 2222)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError(_("The unlock SSH port must be between 1 and 65535."))
    if mode != "none" and port == 22:
        raise ValueError(_("Early-boot SSH must use a different port from ordinary SSH (22)."))
    if rpi:
        import re
        for field in ("initrdHostKey", "recoveryHostKey"):
            key = value.get(field)
            if key is not None and (not isinstance(key, str) or not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/]+=*( [^\n\r]*)?", key)):
                raise ValueError(_("RPi host-key pins must be public Ed25519 SSH keys."))
    return {**value, "mode": mode, "unlock": unlock, "port": port}


def require_supported(target, method="image"):
    security = policy(target)
    if security["mode"] == "confidential":
        raise RuntimeError(_("Confidential deployment is unavailable: measured launch and attestation-bound key delivery are not implemented. No VM was created and no key was released."))
    if security["mode"] != "none" and method != "image":
        raise RuntimeError(_("Encrypted ISO installation is unavailable; use an encrypted image prepared on a trusted local machine."))
    return security


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def binding(target):
    return {"backend": target["backend"], "uuid": target["spec"]["uuid"],
            "guestHostName": target.get("guestHostName", target["spec"]["name"]),
            "configuration": target.get("configuration", "default"),
            "network": target.get("network", {}),
            "diskGiB": target["spec"]["diskGiB"],
            "security": {k: v for k, v in policy(target).items() if k != "imageDirectory"}}


def image_metadata(target, *, name="TARGET"):
    """Read preparation metadata with an actionable error before adapter dispatch."""
    security = require_supported(target)
    directory = security.get("imageDirectory")
    if security["mode"] != "luks" or not directory:
        raise RuntimeError(_("Set security.imageDirectory to the prepared encrypted image directory."))
    root = Path(directory)
    if not root.is_absolute() or root.is_symlink():
        raise RuntimeError(_("The encrypted image directory must be an absolute regular directory."))
    metadata_path = root / "encryption.json"
    try:
        metadata = json.loads(metadata_path.read_text())
    except FileNotFoundError:
        raise RuntimeError(_("No prepared encrypted image metadata was found at {path}. Run {command} first; its output directory must not already exist.").format(
            path=metadata_path, command=f"nixberry prepare-image {name}")) from None
    return root, metadata


def artifact(target, *, system=None):
    root, metadata = image_metadata(target)
    if metadata["binding"] != binding(target) or (system is not None and metadata["systemPath"] != system):
        raise RuntimeError(_("The encrypted image does not match this declaration and system; prepare a new image."))
    path = root / ("system." + FORMATS[target["backend"]])
    if path.is_symlink() or not path.is_file() or digest(path) != metadata["sha256"]:
        raise RuntimeError(_("The encrypted image checksum does not match; refusing deployment."))
    return path, metadata


def validate_manifest(manifest):
    if policy(manifest)["mode"] == "none":
        return
    require_supported(manifest, manifest.get("deploymentMethod", "image"))
    path, metadata = artifact(manifest["securityTarget"], system=manifest["systemPath"])
    declared = Path(manifest["imagePath"])
    if declared != path and declared != path.parent:
        raise RuntimeError(_("The deployment media path does not match the verified encrypted image."))


def encryption_checks(target):
    """Verify the actual root mapping and its backing disk, not only its name."""
    if policy(target)["mode"] == "none":
        return []
    checks = [
        'test "$(readlink -f "$(findmnt -n -o SOURCE /)")" = "$(readlink -f /dev/mapper/nixberry-root)"',
        "LC_ALL=C cryptsetup status nixberry-root | grep -Eq '^[[:space:]]*type:[[:space:]]*LUKS2$'",
        'test "$(readlink -f "$(LC_ALL=C cryptsetup status nixberry-root | sed -n "s/^[[:space:]]*device:[[:space:]]*//p")")" = "$(readlink -f /dev/disk/by-partlabel/disk-system-root)"',
        'test "$(LC_ALL=C cryptsetup luksDump /dev/disk/by-partlabel/disk-system-root | sed -n "s/^Version:[[:space:]]*//p")" = 2',
        'test -n "$(lsblk -ndo PKNAME /dev/disk/by-partlabel/disk-system-root)"',
        'test "$(lsblk -ndo PKNAME /dev/disk/by-partlabel/disk-system-root)" = "$(lsblk -ndo PKNAME /dev/disk/by-partlabel/disk-system-boot)"',
        'test "$(readlink -f "$(findmnt -n -o SOURCE /boot)")" = "$(readlink -f /dev/disk/by-partlabel/disk-system-boot)"',
    ]
    if target["backend"] == "rpi":
        import shlex
        spec = target["spec"]
        root = shlex.quote("/dev/disk/by-partlabel/" + spec.get("rootLabel", spec["hostName"] + "-root"))
        boot = shlex.quote("/dev/disk/by-partlabel/" + spec.get("bootLabel", spec["hostName"] + "-boot"))
        checks = [line.replace("/dev/disk/by-partlabel/disk-system-root", root)
                  .replace("/dev/disk/by-partlabel/disk-system-boot", boot) for line in checks]
    return checks


@contextmanager
def secret_directory():
    # Disko copies input secrets to TMPDIR; force that directory onto memory storage.
    for root in (os.environ.get("XDG_RUNTIME_DIR"), "/dev/shm", "/tmp"):
        if not root or not Path(root).is_dir():
            continue
        result = subprocess.run(["stat", "-f", "-c", "%T", root], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        if result.returncode == 0 and result.stdout.strip() == "tmpfs":
            with tempfile.TemporaryDirectory(prefix="nixberry-secrets-", dir=root) as directory:
                os.chmod(directory, 0o700)
                yield Path(directory)
            return
    raise RuntimeError(_("Encrypted image preparation requires a writable tmpfs for temporary secrets."))


def read_password(key_file=None):
    if key_file:
        path = Path(key_file)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            state = os.fstat(stream.fileno())
            if not stat.S_ISREG(state.st_mode) or state.st_uid != os.getuid() or state.st_mode & 0o077:
                raise ValueError(_("The password file must be a private regular file owned by the current user."))
            password = stream.read(4097).rstrip(b"\n")
    else:
        import sys
        if not sys.stdin.isatty():
            raise ValueError(_("Interactive passphrase entry requires a terminal; use --key-file for automation."))
        password = getpass.getpass(_("New LUKS passphrase: ")).encode()
        if password != getpass.getpass(_("Repeat LUKS passphrase: ")).encode():
            raise ValueError(_("The passphrases do not match."))
    if not 16 <= len(password) <= 4096 or b"\n" in password or b"\0" in password:
        raise ValueError(_("Use a single-line LUKS passphrase of 16 to 4096 bytes."))
    return password


def prepare_image(recipe, name, target, destination, *, key_file=None, dry_run=False):
    require_supported(target)
    if target["backend"] == "rpi":
        raise ValueError(_("Raspberry Pi encryption uses its recovery installer, not VM image preparation."))
    if policy(target)["mode"] != "luks":
        raise ValueError(_("prepare-image requires security.mode = luks."))
    destination = Path(destination).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError(_("The output directory already exists; refusing to overwrite it."))
    recipe = Path(recipe).resolve()
    if key_file:
        key_path = Path(key_file).expanduser().resolve()
        if recipe == key_path or recipe in key_path.parents or str(key_path).startswith("/nix/store/"):
            raise ValueError(_("Keep the runtime password file outside the recipe and Nix store."))
        key_file = str(Path(key_file).expanduser().absolute())
    if recipe == destination or recipe in destination.resolve().parents:
        raise ValueError(_("Store encrypted images and secrets outside the recipe root."))
    command = ["nix", "build", "--no-link", "--print-out-paths", "--no-write-lock-file",
               "path:" + str(recipe) + "#nixberryImageBuilders." + name]
    plan = {"target": name, "destination": str(destination), "security": policy(target), "build": command}
    if dry_run:
        return plan
    password = read_password(key_file)
    # Freeze before evaluation, so the builder, policy and metadata describe one recipe.
    from updater import freeze_recipe
    with tempfile.TemporaryDirectory(prefix="nixberry-encrypted-build-") as capture:
        frozen = freeze_recipe(str(recipe), name, target, capture)
        command[-1] = "path:" + frozen + "#nixberryImageBuilders." + name
        builder = subprocess.check_output(command, text=True).strip()
        system = subprocess.check_output(["nix", "eval", "--raw", "--no-write-lock-file",
            "path:" + frozen + "#nixberrySystems." + name + ".config.system.build.toplevel.outPath"], text=True).strip()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".nixberry-encrypted-", dir=destination.parent) as staging:
            staging = Path(staging)
            with secret_directory() as secrets:
                key = secrets / "password"
                key.write_bytes(password)
                key.chmod(0o600)
                del password
                host_key = secrets / "ssh_host_ed25519_key"
                subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-C", "nixberry-initrd",
                                "-N", "", "-f", str(host_key)], check=True)
                environment = {**os.environ, "TMPDIR": str(secrets)}
                subprocess.run([builder, "--pre-format-files", str(key), "/run/nixberry/luks-password",
                    "--post-format-files", str(host_key), "/etc/secrets/initrd/ssh_host_ed25519_key"],
                    cwd=staging, env=environment, check=True, stdout=subprocess.DEVNULL)
                public_key = host_key.with_suffix(".pub").read_text().strip()
            source = staging / "system.qcow2"
            extension = FORMATS[target["backend"]]
            output = staging / ("system." + extension)
            if output != source:
                conversion = ["qemu-img", "convert", "-f", "qcow2", "-O", extension]
                if extension == "vmdk":
                    conversion += ["-o", "subformat=monolithicSparse"]
                subprocess.run([*conversion, str(source), str(output)], check=True)
                source.unlink()
            output.chmod(0o600)
            metadata = {"binding": binding(target), "systemPath": system,
                        "initrdHostKey": public_key, "sha256": digest(output)}
            (staging / "encryption.json").write_text(json.dumps(metadata, indent=2) + "\n")
            destination.mkdir(mode=0o700)
            for path in staging.iterdir():
                shutil.move(str(path), destination / path.name)
    info(_("Encrypted image prepared. Keep the passphrase separately; verify a complete locked boot and remote unlock before relying on it."))
    return plan | {"systemPath": system, "initrdHostKey": public_key}


def unlock(target, address, *, dry_run=False):
    security = require_supported(target)
    if security["mode"] != "luks":
        raise ValueError(_("unlock requires security.mode = luks."))
    # A pinned public key comes from trusted local preparation, never ssh-keyscan.
    if target["backend"] == "rpi":
        key = security.get("initrdHostKey")
        if not key:
            raise ValueError(_("Set security.initrdHostKey to the trusted RPi boot SSH public key before unlocking."))
        metadata = {"initrdHostKey": key}
        identity = "rpi-" + target["spec"]["hardwareSerial"]
    else:
        image, metadata = artifact(target)
        identity = target["spec"]["uuid"]
    import ipaddress
    address = str(ipaddress.ip_address(address))
    if "%" in address:
        raise ValueError(_("Use an IPv4 or unscoped IPv6 guest address."))
    if address in ("192.168.79.1", target["spec"].get("sshHost")):
        raise ValueError(_("The unlock address must identify the guest, not the hypervisor or management router."))
    alias = "nixberry-initrd-" + identity
    with tempfile.TemporaryDirectory(prefix="nixberry-unlock-") as directory:
        known = Path(directory) / "known_hosts"
        known.write_text(alias + " " + metadata["initrdHostKey"] + "\n")
        command = ["ssh", "-t", "-p", str(security["port"]),
            "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + str(known),
            "-o", "GlobalKnownHostsFile=/dev/null", "-o", "HostKeyAlias=" + alias,
            "-o", "KnownHostsCommand=none", "-o", "VerifyHostKeyDNS=no",
            "-o", "HostKeyAlgorithms=ssh-ed25519", "-o", "UpdateHostKeys=no",
            "-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "ControlPersist=no",
            "-o", "ClearAllForwardings=yes", "-o", "ForwardAgent=no", "-o", "ForwardX11=no",
            "-o", "ConnectTimeout=10", "root@" + address]
        spec = target["spec"]
        if target["backend"] == "hyperv" or (target["backend"] in ("proxmox", "virtualbox", "vmware") and spec.get("transport") == "ssh"):
            # Use the same declared transport as normal VM management.
            from runtime import addresses
            import re
            if addresses([spec["sshHost"]]) != [spec["sshHost"]] or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", spec["sshUser"]):
                raise ValueError(_("Disallowed SSH host or account."))
            import shlex
            relay = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                     "-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "ControlPersist=no",
                     "-o", "ForwardAgent=no", "-o", "ForwardX11=no", "-W", "[%h]:%p",
                     spec["sshUser"] + "@" + spec["sshHost"]]
            command[1:1] = ["-o", "ProxyCommand=" + shlex.join(relay)]
        if dry_run:
            return {"address": address, "port": security["port"], "hostKeyAlias": alias,
                    "initrdHostKey": metadata["initrdHostKey"]}
        return subprocess.run(command, check=False).returncode


def unlock_for_install(manifest, address):
    """Keep installation success dependent on the normal installed-system checks."""
    if policy(manifest)["mode"] == "none":
        return
    if not address:
        network = manifest["securityTarget"].get("network", {})
        if network.get("mode") == "static":
            import ipaddress
            address = str(ipaddress.ip_interface(network["address"]).ip)
    import sys
    if not address or not sys.stdin.isatty():
        raise ValueError(_("The encrypted VM's boot is not verified. If it is waiting for a passphrase, run unlock TARGET --address IP in a terminal, then repeat install --apply."))
    info(_("Unlocking the encrypted VM through its pinned early-boot SSH identity…"))
    # SSH may disconnect during switch-root; the caller verifies boot independently.
    unlock(manifest["securityTarget"], address)
