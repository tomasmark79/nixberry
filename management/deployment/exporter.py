"""Portable bundle of a prebuilt image, pinned recipe and checksums."""

from messages import _, info, shell_script
from runtime import deployment_method
from recipe_source import materialized_recipe

import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile


def export(recipe, target_name, target, destination, *, dry_run=False, method=None):
    destination = Path(destination).expanduser()
    if destination.is_symlink():
        raise RuntimeError(_("The output directory already exists; export will not overwrite it."))
    recipe, destination = Path(recipe).resolve(), destination.resolve()
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", target_name):
        raise RuntimeError(_("Disallowed exported target name."))
    if destination.exists() or destination.is_symlink():
        raise RuntimeError(_("The output directory already exists; export will not overwrite it."))
    if destination == recipe or recipe in destination.parents:
        raise RuntimeError(_("Store the bundle outside the NixBerry recipe root."))
    selected = method or deployment_method(target)
    from security import require_supported, artifact, policy
    security = require_supported(target, selected)
    attribute = ("nixberryInstallers." if selected == "iso" and target["backend"] != "rpi" else "nixberryImages.") + target_name
    command = ["nix", "build", "--print-out-paths", "--no-write-lock-file",
               "path:" + str(recipe) + "#" + attribute]
    if dry_run:
        with materialized_recipe(recipe) as source:
            if source != str(recipe):
                metadata = json.loads(subprocess.check_output([
                    "nix", "flake", "metadata", "--json", "--no-write-lock-file", "path:" + source], text=True))
                command[-1] = "path:" + metadata["path"] + "#" + attribute
        return {"target": target_name, "destination": str(destination), "deploymentMethod": selected,
                "configuration": target.get("configuration", "default"),
                "allowReset": target.get("allowReset", False),
                **({"security": security} if security["mode"] != "none" else {}),
                **({"preparedImageDirectory": security.get("imageDirectory")}
                   if security["mode"] != "none" and target["backend"] != "rpi" else {"build": [*command[:-1], "--no-link", command[-1]]})}
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging_directory = tempfile.TemporaryDirectory(prefix=".nixberry-export-", dir=destination.parent)
    except PermissionError as error:
        raise RuntimeError(_("Cannot write to bundle location {path}. Relative paths are resolved from the working directory. Choose a writable directory, for example --output ~/{target}-offline.").format(
            path=destination, target=target_name)) from error
    info(_("Preparing the offline bundle for target {target} in {path}."), target=target_name, path=destination)
    with staging_directory as staging:
        # Nix creates one immutable source snapshot. Do not read the working recipe again.
        with materialized_recipe(recipe) as source:
            metadata = json.loads(subprocess.check_output([
                "nix", "flake", "metadata", "--json", "--no-write-lock-file", "path:" + source], text=True))
        snapshot = Path(metadata["path"])
        staging = Path(staging)
        # GC roots live outside bundle content and remain until copying completes.
        subprocess.run(["nix-store", "--add-root", str(staging / "source"),
                        "--indirect", "--realise", str(snapshot)], check=True, stdout=subprocess.DEVNULL)
        root = staging / "bundle"
        root.mkdir()
        frozen_recipe = root / "recipe"
        shutil.copytree(snapshot, frozen_recipe, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", "*.log", ".git", "result", "result-*"))
        # Nix store directories are read-only; this private copy must be movable.
        for directory in [frozen_recipe, *(path for path in frozen_recipe.rglob("*") if path.is_dir())]:
            directory.chmod(directory.stat().st_mode | stat.S_IWUSR)
        flake = "path:" + str(frozen_recipe)
        # The declaration comes from the image snapshot, not a previously built CLI.
        target = json.loads(subprocess.check_output([
            "nix", "eval", "--json", "--no-write-lock-file",
            flake + "#nixberryDeployment.targets." + target_name], text=True))
        backend = target["backend"]
        selected = method or deployment_method(target)
        attribute = ("nixberryInstallers." if selected == "iso" and backend != "rpi" else "nixberryImages.") + target_name
        extensions = {"rpi": "img.zst", "libvirt": "qcow2", "hyperv": "vhdx",
                      "proxmox": "qcow2", "virtualbox": "vdi", "vmware": "vmdk"}
        if backend not in extensions:
            raise RuntimeError(_("Image export is not implemented for this platform."))
        extension = "iso" if selected == "iso" and backend != "rpi" else extensions[backend]
        encrypted = policy(target)["mode"] != "none" and backend != "rpi"
        if encrypted:
            artifacts = [artifact(target)[0]]
        else:
            result = subprocess.run([
                *command[:-1], "--out-link", str(staging / "image"),
                flake + "#" + attribute], text=True, stdout=subprocess.PIPE, check=True)
            paths = result.stdout.strip().splitlines()
            if len(paths) != 1:
                raise RuntimeError(_("The build did not return exactly one image."))
            artifacts = list(Path(paths[0]).rglob("*." + extension))
        if len(artifacts) != 1:
            raise RuntimeError(_("The result does not contain exactly one image of the expected format."))
        system = subprocess.check_output(["nix", "eval", "--raw", "--no-write-lock-file",
            flake + "#nixberrySystems." + target_name + ".config.system.build.toplevel.outPath"], text=True).strip()
        filename = ("system" if encrypted else "nixberry-" + target_name) + "." + extension
        if encrypted:
            prepared_path, encryption = artifact(target, system=system)
            shutil.copyfile(Path(target["security"]["imageDirectory"]) / "encryption.json", root / "encryption.json")
        shutil.copyfile(artifacts[0], root / filename)
        manifest = {"target": target_name, "backend": backend, "profile": target["profile"], "deploymentMethod": selected,
                    "configuration": target.get("configuration", "default"),
                    "allowReset": target.get("allowReset", False),
                    "artifact": filename, "format": extension, "spec": target["spec"],
                    "guestHostName": target.get("guestHostName", target["spec"].get("name")),
                    "network": target.get("network", {"mode": "dhcp"}) if backend != "rpi" else None,
                    "systemPath": system,
                    "lockSha256": hashlib.sha256((frozen_recipe / "flake.lock").read_bytes()).hexdigest()}
        if encrypted:
            manifest["security"] = policy(target)
            manifest["initrdHostKey"] = encryption["initrdHostKey"]
        elif backend == "rpi" and policy(target)["mode"] != "none":
            manifest["security"] = policy(target)
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        (root / "README.txt").write_text(_(
            "NixBerry: offline bundle\n\n1. Verify checksums: sha256sum -c SHA256SUMS\n2. Transfer and boot instructions: recipe/README.md\n3. Target and parameters: manifest.json\n\nThe image contains the system and its dependencies. VM disks boot directly;\nthe RPi SD card is a recovery environment with the prebuilt target system.\nInstalled services and applications may have their own network requirements.\n"
        ) + (_("\nProxmox handoff: see PROXMOX.txt.\n") if backend == "proxmox" else "")
          + (_("\nVirtualBox handoff: see VIRTUALBOX.txt.\n") if backend == "virtualbox" else ""))
        if extension == "iso":
            disko = subprocess.check_output(["nix", "eval", "--raw", "--no-write-lock-file",
                flake + "#nixberrySystems." + target_name + ".config.system.build.diskoScript.outPath"], text=True).strip()
            manifest["diskoPath"] = disko
            (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            (root / "INSTALL-ISO.sh").write_text(shell_script(Path(__file__).with_name("guest-install.sh")))
            disk = "/dev/vda" if backend in ("proxmox", "libvirt") else "/dev/sda"
            (root / "README.txt").write_text(_(
                "NixBerry: offline ISO installation\n\n1. Verify sha256sum -c SHA256SUMS.\n2. Create a new VM with UEFI, Secure Boot disabled and exactly one blank disk.\n   CPU, RAM, disk and network parameters are in manifest.json; the target disk must be {disk}.\n   Use the guest agent and controller for the platform described in recipe/README.md.\n3. Attach {filename} and boot the VM from it. The ISO already contains the system and disko.\n4. Transfer INSTALL-ISO.sh to the installation VM over SSH or another medium.\n   First verify the VM identity in the hypervisor console and its actual DMI UUID:\n   cat /sys/class/dmi/id/product_uuid\n5. Read-only check (replace UUID with the verified value):\n   bash INSTALL-ISO.sh dry-run UUID {system} {disko} {bytes} {profile} {disk}\n   After checking, repeat the same command with apply instead of dry-run.\n   Installation refuses disks with partitions or signatures; it does not reinstall an existing system.\n6. Shut down the VM gracefully, detach the ISO and boot from disk.\n   Verify networking and readlink -f /run/current-system against manifest.json.\n\nThe operator prepares the hypervisor network. System networking follows manifest.json.\n"
            ).format(disk=disk, filename=filename, system=system, disko=disko,
                     bytes=target["spec"]["diskGiB"] * 1024**3, profile=target["profile"]))
        named_adapters = target["spec"].get("networkAdapters") if backend != "rpi" else None
        adapter_text = (_("Attach every declared adapter in order, preserving its MAC and attachment; see NETWORKS.txt.")
                        if named_adapters else _("Attach exactly one network adapter to the chosen network."))
        identity_text = (_("The operator may change the VM UUID, but must preserve every declared adapter MAC.")
                         if named_adapters else _("The operator may assign a different MAC and UUID; system networking is not tied to the MAC."))
        if backend == "virtualbox" and extension != "iso":
            network = manifest["network"]
            network_text = (_("Static IP: {address}\nGateway: {gateway}\nDNS: {dns}\n").format(
                address=network["address"], gateway=network["gateway"], dns=", ".join(network["dns"]))
                if network.get("mode", "dhcp") == "static" else _("VM system network: DHCP.\n"))
            (root / "VIRTUALBOX.txt").write_text(_(
                "System disk handoff for VirtualBox on Linux and Windows\n\nImage: {filename}\nHostname: {hostname}\n{network}\n1. Verify SHA256SUMS before import. On Linux: sha256sum -c SHA256SUMS.\n   On Windows, compare Get-FileHash -Algorithm SHA256 '{filename}' with its SHA256SUMS entry.\n2. Create a new Linux (64-bit) VM, skip unattended installation and do not attach an ISO.\n   Resources: {vcpus} CPUs, {memory} MiB RAM. Enable UEFI and leave Secure Boot disabled.\n3. First copy the VDI outside the bundle; attach this existing disk to the SATA controller.\n   The image has {disk} GiB capacity and contains the installed system.\n   For another VM, use VBoxManage clonemedium disk to assign a unique media UUID.\n4. {adapters}\n   For DHCP, use Bridged Adapter with DHCP or NAT.\n   For a static IP, the network must match the listed subnet and gateway.\n5. Boot the VM from disk and verify ip -4 address, ip route and systemctl status virtualbox in its console.\n   The expected system (readlink -f /run/current-system) is in manifest.json.\n\nFor NAT, configure SSH port forwarding in the operator's environment.\n{identity}\nManual import does not adopt ownership markers for automatic hypervisor management.\nManage the server through access in the offline declaration or --ssh root@HOST:PORT.\nSSH accepts recipe keys; password login is disabled.\n"
            ).format(filename=filename, hostname=manifest["guestHostName"], network=network_text, adapters=adapter_text, identity=identity_text,
                     vcpus=target["spec"]["vcpus"], memory=target["spec"]["memoryMiB"], disk=target["spec"]["diskGiB"]))
        if backend == "proxmox" and extension != "iso":
            network = manifest["network"]
            network_text = (_("Static IP: {address}\nGateway: {gateway}\nDNS: {dns}\n").format(
                address=network["address"], gateway=network["gateway"], dns=", ".join(network["dns"]))
                if network.get("mode", "dhcp") == "static" else _("VM system network: DHCP.\n"))
            (root / "PROXMOX.txt").write_text(_(
                "System disk handoff for VM{vmid}\n\nImage: {filename}\nHostname: {hostname}\n{network}\n1. Verify sha256sum -c SHA256SUMS.\n2. Prepare a powered-off VM with UEFI/OVMF and Secure Boot disabled.\n   Recommended resources: {vcpus} CPUs, {memory} MiB RAM, {disk} GiB disk.\n3. Import QCOW2 into the chosen storage and attach it as virtio0 or scsi0 (VirtIO SCSI).\n   Configure boot from the attached disk and enable QEMU Guest Agent.\n4. {adapters}\n   For a static IP, the connected network must match the listed subnet and gateway.\n5. In the console, verify ip -4 address, ip route and resolvectl status.\n   System: readlink -f /run/current-system; the expected value is in manifest.json.\n\n{identity}\nManual import does not adopt ownership markers required for hypervisor management.\nManage the server through access in its declaration or nixberry update TARGET --ssh root@HOST:PORT --apply.\nSSH access uses keys from the included recipe; password login is disabled.\nThe image supplies the system; the operator manages the host, bridge and firewall.\n"
            ).format(vmid=target["spec"]["vmid"], filename=filename, hostname=manifest["guestHostName"],
                     network=network_text, adapters=adapter_text, identity=identity_text, vcpus=target["spec"]["vcpus"], memory=target["spec"]["memoryMiB"],
                     disk=target["spec"]["diskGiB"]))
        if named_adapters:
            (root / "NETWORKS.txt").write_text(_(
                "Named VM network adapters\n\nCreate all adapters in the listed order and preserve their MAC addresses.\nThe first adapter provides management access and uses the top-level network settings.\nSecondary adapters use network.interfaces; omitted entries have no address or DHCP.\nPrepare attachments in the destination environment; manual import does not adopt hypervisor ownership.\nVMware address discovery is not MAC-specific; SSH verification rejects a secondary-interface endpoint.\n\n"
            ) + json.dumps({"networkAdapters": named_adapters, "network": manifest["network"]}, indent=2) + "\n")
            with (root / "README.txt").open("a") as stream:
                stream.write(_("\nNamed adapters: read NETWORKS.txt and preserve all declared MAC addresses.\n"))
        info(_("Computing bundle checksums…"))
        lines = []
        for file in sorted(root.rglob("*")):
            if file.is_file():
                with file.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                lines.append(digest + "  " + str(file.relative_to(root)))
        (root / "SHA256SUMS").write_text("\n".join(lines) + "\n")
        # A destination occupied during a long build must not be overwritten.
        destination.mkdir(mode=0o700 if policy(target)["mode"] != "none" else 0o777)
        try:
            for item in root.iterdir():
                shutil.move(str(item), destination / item.name)
        except BaseException:
            # Leave partial results for inspection; never remove existing content.
            raise
    return manifest | {"destination": str(destination)}
