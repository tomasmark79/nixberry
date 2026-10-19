"""Aktualizace instalovaného systému z projektového receptu přes ověřené SSH."""

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

from runtime import installation_checks, read, ssh_command, status, verify, guest_host_option, direct_connection
from messages import _, info, success, warning


def system_path(value):
    if not re.fullmatch(r"/nix/store/[a-z0-9]{32}-nixos-system-[A-Za-z0-9+._-]+", value):
        raise RuntimeError(_("Cíl nebo sestavení nevrátilo platnou cestu systémové generace."))
    return value


def connection(target, snapshot):
    command = ssh_command(target, snapshot)
    return [command[0], "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=10", *command[1:]]


def preflight(target, snapshot):
    # status ověřuje identitu; zde ještě odmítneme instalační ISO a obnovovací SD.
    checks = installation_checks(target)
    architecture = "aarch64" if target["backend"] == "rpi" else "x86_64"
    checks.append('test "$(uname -m)" = ' + shlex.quote(architecture))
    guarded = [check + " || { printf '%s\\n' " + shlex.quote(_("Kontrola instalace neprošla: {check}").format(check=check))
               + "; exit 1; }" for check in checks]
    output = read(connection(target, snapshot) + [
        "set -eu; " + "; ".join(guarded + ["readlink -f /run/current-system"])])
    return system_path(output)


def build(recipe, name, directory):
    attribute = "path:" + recipe + "#nixberrySystems." + name + ".config.system.build.toplevel"
    link = Path(directory) / "system"
    # Dočasný GC root drží výsledek při přenosu i aktivaci, build nemá časový limit SSH.
    subprocess.run(["nix", "build", "--no-write-lock-file", "--out-link", str(link), attribute],
                   check=True)
    return system_path(str(link.resolve(strict=True)))


def freeze_recipe(recipe, name, target, directory):
    metadata = json.loads(subprocess.check_output([
        "nix", "flake", "metadata", "--json", "--no-write-lock-file", "path:" + recipe], text=True))
    frozen = metadata["path"]
    # Snímek i jeho sestavení držíme proti GC až do dokončení operace.
    subprocess.run(["nix-store", "--add-root", str(Path(directory) / "recipe"),
                    "--indirect", "--realise", frozen], check=True, stdout=subprocess.DEVNULL)
    declared = json.loads(subprocess.check_output([
        "nix", "eval", "--json", "--no-write-lock-file",
        "path:" + frozen + "#nixberryDeployment.targets." + name], text=True))
    # Příkazy pocházejí z balíčku CLI; jejich platnost zaručuje shodná deklarace.
    identity = {key: value for key, value in target.items()
                if key not in ("commands", "operations", "capabilities", "_guestConnection")}
    declared_identity = {key: value for key, value in declared.items() if key != "capabilities"}
    if declared_identity != identity:
        raise RuntimeError(
            _("Deklarace cíle se liší od spuštěného CLI. Spusť příkaz znovu přes aktuální spouštěč nixberry."))
    return frozen


def reboot_required(command):
    script = (
        "set -eu; test -d /run/booted-system; "
        'if [ "$(readlink -f /run/booted-system/kernel)" != '
        '"$(readlink -f /run/current-system/kernel)" ] || '
        '[ "$(readlink -f /run/booted-system/initrd)" != '
        '"$(readlink -f /run/current-system/initrd)" ] || '
        '! cmp -s /run/booted-system/kernel-params /run/current-system/kernel-params; '
        "then printf 'yes\\n'; else printf 'no\\n'; fi"
    )
    value = read(command + [script])
    if value not in ("yes", "no"):
        raise RuntimeError(_("Nepodařilo se zjistit potřebu restartu."))
    return value == "yes"


def update(recipe, name, target, *, apply=False):
    if target["backend"] not in ("rpi", "libvirt", "hyperv", "proxmox", "virtualbox", "vmware"):
        raise RuntimeError(_("Aktualizace této platformy není implementovaná."))
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", name):
        raise RuntimeError(_("Neplatné jméno cíle pro aktualizaci."))
    if not recipe or not Path(recipe, "flake.nix").is_file() or not Path(recipe, "flake.lock").is_file():
        raise RuntimeError(_("update spusť přes spouštěč nixberry s úplným připnutým receptem."))
    info(_("Aktualizace systému: {name} ({backend})"), name=name, backend=target["backend"])
    info(_("Konfigurace: {configuration}"),
         configuration=f"path:{recipe}#nixberrySystems.{name}.config.system.build.toplevel")
    host_option = guest_host_option(target)
    if not apply:
        info(_("Plán: ověřit běžící instalaci → sestavit → přenést přes SSH → aktivovat → ověřit."))
        direct = direct_connection(target)
        if direct:
            info(_("Přímé SSH do VM: {endpoint}; ověřím SSH klíč a deklarovaný hostname."),
                 endpoint=f'{direct["user"]}@{direct["host"]}:{direct["port"]}')
        else:
            info(_("Adresa se zjistí z deklarace a živého stavu; Hyper-V použije Windows jako jump host."))
        info(_("Bez --apply se nepřipojuji k cíli, nesestavuji ani neměním systém."))
        info(_("Provedení: {command}"), command=f"nixberry update {name}{host_option} --apply")
        return None

    with tempfile.TemporaryDirectory(prefix="nixberry-update-") as directory:
        frozen = freeze_recipe(recipe, name, target, directory)
        snapshot = status(target)
        previous = preflight(target, snapshot)
        info(_("Původní systém: {system}"), system=previous)
        info(_("Sestavuji konfiguraci cíle…"))
        system = build(frozen, name, directory)
        # Dlouhé sestavení nesmí způsobit aktivaci na zastaralé adrese nebo cizí VM.
        current_snapshot = status(target)
        current = preflight(target, current_snapshot)
        if current != previous:
            raise RuntimeError(_("Během sestavení se systém cíle změnil. Spusť update znovu."))
        command = connection(target, current_snapshot)
        destination = "ssh://" + command[-1]
        ssh_options = shlex.join(command[1:command.index("--")])
        info(_("Přenáším systém na {host}…"), host=command[-1])
        subprocess.run(["nix", "copy", "--to", destination, system], check=True,
                       env={**os.environ, "NIX_SSHOPTS": ssh_options})
        final_snapshot = status(target)
        if (final_snapshot["addresses"] != current_snapshot["addresses"]
                or preflight(target, final_snapshot) != previous):
            raise RuntimeError(_("Během přenosu se cíl změnil. Aktivace neproběhla; spusť update znovu."))
        activation = (
            'set -eu; test "$(readlink -f /run/current-system)" = ' + shlex.quote(previous)
            + "; nix-env -p /nix/var/nix/profiles/system --set " + shlex.quote(system)
            + "; " + shlex.quote(system + "/bin/switch-to-configuration") + " switch"
        )
        # Samostatná systemd jednotka nechá aktivaci doběhnout i při restartu sshd.
        # Nepřebírá PATH z SSH shellu; bez explicitního prostředí nenajde nix-env.
        remote = (
            "systemd-run --unit=nixberry-update --collect --wait "
            "--setenv=PATH=/run/current-system/sw/bin:/run/wrappers/bin "
            "--property=Type=oneshot -- /bin/sh -c " + shlex.quote(activation)
        )
        info(_("Aktivuji novou generaci bez restartu…"))
        activated = subprocess.run(command + [remote], check=False)
        if activated.returncode:
            raise RuntimeError(
                _("Aktivaci se nepodařilo potvrdit (SSH nebo systemd-run). "
                  "Zkontroluj {logs} a {verify}. "
                  "Generace již mohla být nastavena; restart jsem neprovedl.").format(
                      logs=f"nixberry ssh {name}{host_option} -- journalctl -u nixberry-update",
                      verify=f"nixberry verify {name}{host_option}"))
        try:
            result = verify(target, system)
            needs_reboot = reboot_required(connection(target, {
                "state": "running", "addresses": result["addresses"]}))
        except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
            raise RuntimeError(
                _("Aktivace doběhla, ale následná kontrola neprošla: {error}. "
                  "Po obnovení SSH spusť {command}.").format(
                      error=error, command=f"nixberry verify {name}{host_option}")) from error
    success(_("Ověřeno: {name}, {hostname}, systém z {root}."),
            name=name, hostname=result["hostname"], root=result["root"])
    print(system)
    if needs_reboot:
        warning(_("Bootovací konfigurace se liší od běžícího systému; je potřeba restart."))
        info(_("Až bude vhodná chvíle: {command}"), command=f"nixberry ssh {name}{host_option} -- reboot")
        info(_("Po startu: {command}"), command=f"nixberry verify {name}{host_option}")
    else:
        info(_("Kernel, initrd a parametry startu odpovídají běžícímu systému; restart není potřeba."))
    return {**result, "previousSystem": previous, "rebootRequired": needs_reboot}
