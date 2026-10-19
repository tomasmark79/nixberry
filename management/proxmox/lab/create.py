#!/usr/bin/env python3
"""Samostatný vnořený Proxmox pro zkoušení adaptéru; nikdy nepřebírá existující VM."""

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deployment"))
from messages import _, ArgumentParser, info, success, error, forward_stderr

NAME = "nixberry-pvelab"
IDENTITY = str(uuid.uuid5(uuid.NAMESPACE_DNS, "pvelab.nixberry.nixon.test"))
MAC = "52:54:00:78:10:01"
ADDRESS = "192.168.78.10"
ROOT = Path("/home/shared/vms/nixberry-pvelab")
STATE = Path.home() / ".local/state/nixberry/proxmox-lab"
ISO_NAME = "proxmox-ve_9.2-1.iso"
ISO_HASH = "4e88fe416df9b527624a175f24c9aa07c714d3332afb1ee3dbf3879573ef2c6c"
URI = "qemu:///system"


def run(argv, **kwargs):
    result = subprocess.run(argv, text=True, capture_output=True, check=False, **kwargs)
    # Asistent Proxmoxu může při chybě vypsat Error: na stderr a přesto vrátit 0.
    if result.returncode or result.stderr.lstrip().startswith("Error:"):
        raise RuntimeError("\n".join(s.strip() for s in (result.stderr, result.stdout) if s.strip()))
    forward_stderr(result.stderr)
    return result.stdout.strip()


def virsh(*args):
    return run(["virsh", "--connect", URI, *args])


def preflight():
    if run(["hostname"]) != "nixon":
        raise RuntimeError(_("Tento lab je vyhrazený pro místní Nixon."))
    if Path("/sys/module/kvm_amd/parameters/nested").read_text().strip() not in ("1", "Y"):
        raise RuntimeError(_("Nested AMD KVM není zapnuté."))
    if NAME in virsh("list", "--all", "--name").splitlines() or IDENTITY in virsh("list", "--all", "--uuid").splitlines():
        raise RuntimeError(_("Proxmox lab už existuje; nic nepřepisuji ani nerestartuji."))
    if NAME in virsh("net-list", "--all", "--name").splitlines():
        raise RuntimeError(_("Síť labu už existuje; částečný stav vyžaduje kontrolu."))
    for path in (ROOT, *ROOT.parents):
        if path.is_symlink():
            raise RuntimeError(_("Úložiště labu nesmí vést přes symbolický odkaz."))
    for name in ("system.qcow2", "data.qcow2"):
        path = ROOT / name
        if path.exists() or path.is_symlink():
            raise RuntimeError(_("Disk labu už existuje: {path}").format(path=path))
    subnet = ipaddress.ip_network("192.168.78.0/24")
    for route in json.loads(run(["ip", "-j", "-4", "route", "show"])):
        if route.get("dst") not in (None, "default") and subnet.overlaps(ipaddress.ip_network(route["dst"], strict=False)):
            raise RuntimeError(_("Podsíť labu koliduje s existující trasou."))
    if shutil.disk_usage(ROOT.parent).free < 100 * 1024 ** 3:
        raise RuntimeError(_("Lab vyžaduje alespoň 100 GiB volné kapacity úložiště."))
    info(_("Plán: {name}, 6 CPU, 12 GiB RAM, disky 32 + 64 GiB, UEFI a samostatná NAT síť."), name=NAME)


def prepare_iso():
    ROOT.mkdir(exist_ok=True)
    iso = ROOT / ISO_NAME
    if iso.is_symlink():
        raise RuntimeError(_("Instalační ISO nesmí být symbolický odkaz."))
    if not iso.exists():
        info(_("Stahuji oficiální instalační ISO Proxmoxu…"))
        temporary = iso.with_suffix(".iso.part")
        with urllib.request.urlopen("https://enterprise.proxmox.com/iso/" + ISO_NAME, timeout=60) as response, temporary.open("xb") as stream:
            shutil.copyfileobj(response, stream)
        temporary.rename(iso)
    with iso.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != ISO_HASH:
            raise RuntimeError(_("Kontrolní součet oficiálního ISO nesouhlasí."))
    STATE.mkdir(mode=0o700, parents=True, exist_ok=False)
    password = secrets.token_urlsafe(24)
    secret = STATE / "root-password"
    with open(secret, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
        stream.write(password + "\n")
    password_hash = run(["openssl", "passwd", "-6", "-stdin"], input=password + "\n")
    pubkey = (Path(__file__).resolve().parents[3] / "admin.pub").read_text().strip()
    answer = STATE / "answer.toml"
    with open(answer, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
        stream.write(
            '[global]\nkeyboard = "en-us"\ncountry = "cz"\ntimezone = "Europe/Prague"\n'
            'fqdn = "pvelab.nixberry.test"\nmailto = "root@pvelab.nixberry.test"\n'
            'root-password-hashed = ' + json.dumps(password_hash) + '\n'
            'root-ssh-keys = [' + json.dumps(pubkey) + ']\n'
            '[network]\nsource = "from-dhcp"\n'
            '[disk-setup]\nfilesystem = "ext4"\ndisk-list = ["vda"]\n')
    run(["proxmox-auto-install-assistant", "validate-answer", str(answer)])
    prepared = ROOT / "proxmox-auto.iso"
    if prepared.exists() or prepared.is_symlink():
        raise RuntimeError(_("Připravené ISO už existuje; nic nepřepisuji."))
    info(_("Připravuji automatickou instalaci pouze na nový systémový disk VM…"))
    run(["proxmox-auto-install-assistant", "prepare-iso", str(iso), "--fetch-from", "iso",
         "--answer-file", str(answer), "--output", str(prepared)])
    prepared.chmod(0o600)
    return prepared


def create(iso):
    network = STATE / "network.xml"
    network.write_text(
        f'<network><name>{NAME}</name><uuid>{uuid.uuid5(uuid.NAMESPACE_DNS, NAME + ".network")}</uuid>'
        '<forward mode="nat"/><bridge name="virbrpve0" stp="on" delay="0"/>'
        '<ip address="192.168.78.1" netmask="255.255.255.0"><dhcp>'
        '<range start="192.168.78.64" end="192.168.78.200"/>'
        f'<host mac="{MAC}" name="pvelab" ip="{ADDRESS}"/>'
        '</dhcp></ip></network>')
    disks = []
    for name, capacity in (("system", "32G"), ("data", "64G")):
        path = ROOT / (name + ".qcow2")
        run(["qemu-img", "create", "-f", "qcow2", str(path), capacity])
        disks.append(path)
    virsh("net-define", str(network))
    virsh("net-start", NAME)
    # Záměrně bez autostartu: lab má vlastní životní cyklus a nespouští se po bootu notebooku.
    info(_("Spouštím novou Proxmox VM a její automatický instalátor…"))
    run(["virt-install", "--connect", URI, "--name", NAME, "--uuid", IDENTITY,
         "--memory", "12288", "--vcpus", "6", "--cpu", "host-passthrough", "--machine", "q35",
         "--boot", "uefi,loader_secure=no,bootmenu.enable=yes,hd,cdrom",
         "--disk", f"path={disks[0]},format=qcow2,bus=virtio,serial=nixberry-pve-os",
         "--disk", f"path={disks[1]},format=qcow2,bus=virtio,serial=nixberry-pve-data",
         "--cdrom", str(iso), "--network", f"network={NAME},model=virtio,mac={MAC}",
         "--serial", f"pty,log.file=/var/log/libvirt/qemu/{NAME}-serial.log,log.append=on",
         "--osinfo", "debian13", "--graphics", "spice,listen=127.0.0.1", "--noautoconsole", "--wait", "0"])
    domain = virsh("dumpxml", NAME)
    (STATE / "domain.xml").write_text(domain)
    success(_("Instalátor běží. Heslo je v souboru {path}; výsledek instalace ještě ověř samostatně."), path=STATE / "root-password")
    print(json.dumps({"name": NAME, "uuid": IDENTITY, "address": ADDRESS, "state": "installer-started"}))


def main():
    parser = ArgumentParser(description=_(__doc__))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true", help=_("Vytvořit nový lab a spustit instalaci"))
    modes.add_argument("--dry-run", action="store_true", help=_("Pouze ověřit plán bez zápisu"))
    args = parser.parse_args()
    preflight()
    if args.apply:
        iso = prepare_iso()
        # Během stahování nebo přípravy ISO mohl vzniknout kolidující prostředek.
        preflight()
        create(iso)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        error(_("Příprava Proxmox labu selhala: {error}"), error=str(exc))
        raise SystemExit(1)
