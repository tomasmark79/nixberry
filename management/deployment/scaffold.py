"""Create a local declaration for another NixBerry instance without writing to a hypervisor."""

from messages import _

import json
from pathlib import Path
import re
import uuid


def configuration_name(recipe, name):
    """Validate local selection without Nix evaluation or hypervisor contact."""
    directory = Path(recipe) / "configurations"
    names = []
    if directory.is_dir():
        for path in directory.iterdir():
            if path.is_symlink() and not path.is_dir():
                continue
            if path.is_file() and path.suffix == ".nix":
                candidate = path.stem
            elif path.is_dir() and (path / "default.nix").is_file() and not (path / "default.nix").is_symlink():
                candidate = path.name
            else:
                continue
            if re.fullmatch(r"[a-z][a-z0-9-]*", candidate):
                if candidate in names:
                    raise RuntimeError(_("The configuration has both a file and a directory; keep only one definition."))
                names.append(candidate)
    if name not in names:
        raise RuntimeError(_("Unknown configuration {name}. Available configurations: {names}.").format(
            name=name, names=", ".join(sorted(names)) or "—"))
    return name


def new_vm(recipe, hosts, registry, name, backend, host=None, *, write=True, offline=False, method=None, configuration="default"):
    if offline and host is not None:
        raise RuntimeError(_("Offline preparation cannot be combined with --host."))
    configuration = configuration_name(recipe, configuration)
    if backend not in ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware"):
        raise RuntimeError(_("Unsupported backend for a new declaration."))
    if not re.fullmatch(r"[a-z](?:[a-z0-9-]{0,38}[a-z0-9])?", name) or name in registry:
        raise RuntimeError(_("Choose a new target name: 1–40 lowercase letters, digits and hyphens, starting with a letter."))
    candidates = [key for key, value in hosts.items() if value["backend"] == backend]
    if backend == "libvirt" and not offline:
        candidates = [key for key in candidates if hosts[key]["connection"].get("uri") == "qemu:///system"]
    if host is None and not offline:
        if len(candidates) != 1:
            raise RuntimeError(_("Select --host from the declared {backend} hosts: {hosts}. Use --offline to prepare without a hypervisor.").format(
                backend=backend, hosts=", ".join(candidates) or "—"))
        host = candidates[0]
    if not offline and host not in candidates:
        if backend == "libvirt" and host in hosts and hosts[host]["backend"] == backend:
            raise RuntimeError(_("This procedure currently supports only local libvirt."))
        raise RuntimeError(_("The host does not match the selected backend."))
    root = Path(recipe)
    directory = root / "instances"
    if not (root / "flake.nix").is_file() or not (directory / "default.nix").is_file():
        raise RuntimeError(_("Run the command from a complete project recipe."))
    identity = uuid.uuid4()
    suffix = identity.hex[-6:]
    mac = ("00:50:56:" + f"{int(suffix[:2], 16) & 0x3f:02x}" + ":" + suffix[2:4] + ":" + suffix[4:]
           if backend == "vmware" else ("52:54:00:" if backend == "libvirt" else "02:79:81:") + ":".join(suffix[i:i + 2] for i in (0, 2, 4)))
    if backend == "hyperv":
        mac = mac.replace(":", "")
    if any(target["spec"].get("mac", "").replace(":", "").lower() == mac.replace(":", "") for target in registry.values()):
        raise RuntimeError(_("The generated MAC conflicts with an existing one; run the command again."))
    def quote(value):
        return json.dumps(value).replace("${", "\\${")
    owner_id = ("nixberry-hyperv-" if backend == "hyperv" else "nixberry-") + str(identity)
    placement = ('  # For hypervisor management add host = "HOST_NAME" from hosts.local.nix.\n'
                 f'  backend = "{backend}";\n'
                 '  # Adjust VM parameters for the deployment environment.\n'
                 + ('  deployment = {\n    storage = "local-lvm";\n    isoStorage = "local";\n    bridge = "vmbr0";\n  };\n' if backend == "proxmox" else '') +
                 '  # After import, configure server access, for example:\n'
                 '  # access = { host = "server.example"; port = 22; user = "root"; };\n'
                 if offline else f"  host = {quote(host)};\n")
    network = ('  # Server network; review before building the image.\n'
               '  # For a static IP set mode = "static" and fill in all addresses below.\n'
               '  network = {\n'
               '    mode = "dhcp";\n'
               '    address = ""; # For example "10.0.1.19/24".\n'
               '    gateway = ""; # For example "10.0.1.1".\n'
               '    dns = [ ]; # For example [ "10.0.1.1" ].\n'
               '  };\n')
    text = ("# Another instance of the shared server recipe; the host is selected separately.\n{\n"
            + placement + f"  configuration = {quote(configuration)};\n" + network + f"  deploymentMethod = {quote(method or 'image')};\n"
            + f"  profile = {quote('nixberry-vm' if backend == 'libvirt' else 'nixberry-' + backend)};\n"
            + "  # Instance removal is disabled by default.\n  allowReset = false;\n"
            + "  spec = {\n" + f"    name = {quote('nixberry-' + name)};\n"
            + f"    uuid = {quote(str(identity))};\n    ownerId = {quote(owner_id)};\n"
            + f"    mac = {quote(mac)};\n    memoryMiB = 4096;\n    vcpus = 4;\n    diskGiB = 32;\n")
    if backend == "hyperv":
        text += "    generation = 2;\n    secureBoot = false;\n"
    if backend == "proxmox":
        used = {t["spec"].get("vmid") for t in registry.values()
                if t.get("backend") == "proxmox" and (offline or t.get("host") == host)}
        vmid = next(number for number in range(1000, 1000000) if number not in used)
        note = ("Check VMID availability in the target cluster before import." if offline
                else "Unused VMID in local inventory; plan checks the live cluster before writing.")
        text += "    # " + note + "\n" + f"    vmid = {vmid};\n"
    text += "  };\n}\n"
    path = directory / (name + ".nix")
    if path.exists() or path.is_symlink():
        raise RuntimeError(_("The declaration already exists; refusing to overwrite it."))
    if write:
        with path.open("x") as stream:
            stream.write(text)
    return path, text


def new_libvirt(recipe, hosts, registry, name, host=None, *, write=True, method=None, configuration="default"):
    """Preserve the entry point for older callers; use the shared declaration generator."""
    return new_vm(recipe, hosts, registry, name, "libvirt", host, write=write,
                  method=method, configuration=configuration)
