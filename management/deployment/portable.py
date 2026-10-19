"""Přenos hotového disku a ovládání nových hypervizorů přes jednotný adaptér."""

from messages import _, ArgumentParser, info, python_script, shell_script, forward_stderr, error as report_error

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from host_agent import validate, image_name


BACKENDS = ("proxmox", "virtualbox", "vmware")


def known_hosts(spec):
    root = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    identity = spec["uuid"] if "uuid" in spec else spec["ownerId"]
    return root / "nixberry/portable" / identity / "known_hosts"


def transport(spec):
    if spec.get("transport") == "local":
        if spec["hostPlatform"] != "linux":
            raise ValueError(_('Místní správce nyní podporuje Linux; Windows používej přes SSH.'))
        return [sys.executable]
    if spec.get("transport") != "ssh":
        raise ValueError(_('transport musí být local nebo ssh.'))
    from runtime import addresses
    if addresses([spec["sshHost"]]) != [spec["sshHost"]] or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", spec["sshUser"]):
        raise ValueError(_('Nepovolený SSH hostitel nebo účet.'))
    python = spec.get("pythonCommand", "python" if spec["hostPlatform"] == "windows" else "python3")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", python):
        raise ValueError(_('pythonCommand musí být název příkazu dostupný v PATH hostitele.'))
    return ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", "--", spec["sshUser"] + "@" + spec["sshHost"], python]


def rpc(target, action, *, image=None, timeout=None, **extra):
    spec, backend = target["spec"], target["backend"]
    if spec.get("transport") == "offline":
        raise RuntimeError(_('Offline cíl nemá připojeného hostitele; použij show nebo export.'))
    validate(spec, backend)
    request = {"spec": spec, "backend": backend, "action": action, "method": target.get("deploymentMethod", "image"), **extra}
    # Zdroj teče stdin, takže nepřekročí limit příkazové řádky Windows.
    source = python_script(Path(__file__).with_name("host_agent.py")).encode()
    source += ("\nentry(json.loads(" + repr(json.dumps(request, ensure_ascii=True)) + "))\n").encode()
    bootstrap = "import sys;exec(sys.stdin.buffer.read(" + str(len(source)) + "))"
    command = transport(spec)
    if spec["transport"] == "ssh":
        python = command.pop()
        command.append(python + ' -c "' + bootstrap + '"')
    else:
        command += ["-c", bootstrap]
    if image is None:
        result = subprocess.run(command, input=source, capture_output=True,
                                timeout=timeout if timeout is not None else (1900 if action == "create" else 150), check=False)
        stdout, stderr, returncode = result.stdout, result.stderr, result.returncode
    else:
        # Nedržíme celý disk v RAM a nezapisujeme kopii do dočasného souboru.
        with tempfile.TemporaryFile() as errors:
            with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors) as process:
                failures = []
                def send():
                    try:
                        with image.open("rb") as stream:
                            process.stdin.write(source)
                            shutil.copyfileobj(stream, process.stdin, length=1024 * 1024)
                    except BrokenPipeError:
                        pass
                    except (OSError, ValueError) as error:
                        failures.append(error)
                    finally:
                        with contextlib.suppress(BrokenPipeError):
                            process.stdin.close()
                writer = threading.Thread(target=send, daemon=True)
                writer.start()
                try:
                    process.wait(timeout=1900)
                    writer.join(timeout=5)
                    stdout = process.stdout.read()
                except BaseException:
                    process.kill()
                    process.wait()
                    raise
                if failures:
                    raise RuntimeError(_('Čtení obrazu při přenosu selhalo: {value1}').format(value1=str(failures[0])))
                errors.seek(0)
                stderr, returncode = errors.read(), process.returncode
    try:
        envelope = json.loads(stdout)
    except ValueError as error:
        raise RuntimeError(_('Hostitelský adaptér nevrátil JSON: {value1}').format(value1=stderr.decode(errors="replace")[-2000:])) from error
    if returncode or not envelope.get("ok"):
        raise RuntimeError((envelope.get("error") or stderr.decode(errors="replace")[-2000:] or _('Adaptér selhal.')))
    forward_stderr(stderr)
    return envelope["result"]


def snapshot(target):
    return rpc(target, "probe")


def inventory(target, registry):
    return rpc(target, "inventory", declared=[item["spec"] for item in registry.values()
               if item.get("host") == target["host"]])


def image_file(manifest):
    if manifest.get("deploymentMethod", "image") == "iso":
        path = Path(manifest["isoPath"])
        if not path.is_file():
            raise RuntimeError(_('Chybí sestavené instalační ISO: {path}').format(path=path))
        return path
    root = Path(manifest["imagePath"])
    path = root / image_name(manifest["backend"])
    if not path.is_file():
        raise RuntimeError(_('Ve výsledku sestavení chybí očekávaný obraz: {value1}').format(value1=str(path)))
    return path


def create(target, manifest):
    image = image_file(manifest)
    info(_('Ověřuji nepřítomnost cíle a prostředky hostitele…'))
    rpc(target, "plan", imageBytes=image.stat().st_size)
    with image.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    rpc(target, "prepare", sha256=digest, imageBytes=image.stat().st_size)
    info(_('Přenáším a ověřuji instalační médium; metoda {method}…'), method=target.get("deploymentMethod", "image"))
    rpc(target, "upload", image=image)
    result = rpc(target, "create")
    if result["state"] != "shut off":
        raise RuntimeError(_('Vytvoření nevrátilo ověřenou vypnutou VM.'))
    if target.get("deploymentMethod", "image") == "iso":
        rpc(target, "start")
        info(_('VM vytvořená a spuštěná z instalačního ISO.'))
    else:
        info(_('VM je vytvořená s hotovým systémovým diskem; zatím nebyla spuštěná.'))


def wait_stopped(target, *, timeout=120):
    deadline = time.monotonic() + timeout
    while True:
        try:
            if rpc(target, "probe")["state"] == "shut off":
                return
        except RuntimeError as failure:
            # VirtualBox při zavírání konzole krátce neumožní LockMachine.
            # Ostatní chyby včetně nesouhlasící identity se neopakují.
            if target["backend"] != "virtualbox" or "VBOX_E_INVALID_OBJECT_STATE" not in str(failure):
                raise
        if time.monotonic() >= deadline:
            raise RuntimeError(_('VM se nevypnula; ISO neodpojujeme.'))
        time.sleep(3)


def install_iso(target, manifest, *, apply):
    from runtime import ssh_command
    snapshot = rpc(target, "installer-check")
    if snapshot["state"] == "shut off":
        if not apply:
            info(_('Metoda iso: install spustí instalační ISO a zapíše systém pouze na ověřený prázdný disk.'))
            return
        rpc(target, "start")
    with tempfile.TemporaryDirectory(prefix="nixberry-installer-ssh-") as directory:
        def guest(snapshot, command, *, input_text=None, capture=False, timeout=30):
            args = ssh_command({**target, "_acceptNewGuestKey": True}, snapshot)
            args[1:1] = ["-o", "BatchMode=yes", "-o", "UserKnownHostsFile=" + directory + "/known_hosts"]
            return subprocess.run(args + [command], input=input_text, text=True, capture_output=capture, timeout=timeout, check=False)
        info(_('Čekám na instalační ISO, guest agenta a SSH…'))
        deadline = time.monotonic() + 300
        while True:
            snapshot = rpc(target, "installer-check")
            if snapshot["state"] == "running" and len(snapshot["addresses"]) == 1:
                result = guest(snapshot, 'test "$(hostname)" = nixberry-installer', capture=True)
                if result.returncode == 0:
                    break
            if time.monotonic() >= deadline:
                raise RuntimeError(_('Instalační ISO není dosažitelné; VM ponechávám beze změny.'))
            time.sleep(3)
        disk = "/dev/vda" if target["backend"] == "proxmox" else "/dev/sda"
        arguments = ["bash", "-s", "--", "dry-run", target["spec"]["uuid"], manifest["systemPath"],
                     manifest["diskoPath"], str(target["spec"]["diskGiB"] * 1024**3), "nixberry-" + target["backend"], disk]
        script = shell_script(Path(__file__).with_name("guest-install.sh"))
        result = guest(snapshot, shlex.join(arguments), input_text=script)
        if result.returncode:
            raise RuntimeError(_('Kontrola instalačního prostředí selhala; disk nebyl změněný.'))
        if not apply:
            return
        rpc(target, "installer-check")
        arguments[3] = "apply"
        result = guest(snapshot, shlex.join(arguments), input_text=script, timeout=1800)
        if result.returncode:
            raise RuntimeError(_('Instalace selhala; ISO zůstává připojené, nic automaticky neopakuji.'))
        result = guest(snapshot, "sync; systemctl poweroff --no-block", capture=True)
        if result.returncode not in (0, 255):
            raise RuntimeError(_('Vypnutí instalační VM selhalo; ISO zůstává připojené.'))
        info(_('Čekám na korektní vypnutí instalační VM; ISO zůstává připojené.'))
        wait_stopped(target)
        info(_('Instalace dokončená. Odpojuji ISO a spouštím systém z disku…'))
        rpc(target, "boot-disk")
    return start_and_verify(target, manifest)


def start_and_verify(target, manifest, *, timeout=300):
    from runtime import verify
    current = snapshot(target)
    if current["state"] == "shut off":
        rpc(target, "start")
    elif current["state"] != "running":
        raise RuntimeError(_('Nejdřív vytvoř VM přes create nebo reproduce.'))
    hosts = known_hosts(target["spec"])
    hosts.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # První přístup do nově vytvořené a přes hypervizor ověřené VM používá
    # TOFU stejně jako ostatní adaptéry. Změněný existující klíč se nepřijme.
    target = {**target, "_acceptNewGuestKey": True}
    info(_('Čekám na guest agenta, SSH a ověření startu z disku…'))
    deadline = time.monotonic() + timeout
    error = None
    while time.monotonic() < deadline:
        try:
            result = verify(target, manifest["systemPath"])
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
            return result
        except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as failure:
            error = failure
            time.sleep(3)
    raise RuntimeError(_('VM byla spuštěna, ale instalaci se nepodařilo ověřit; nic nezastavuji ani neopakuji vytvoření: {value1}').format(value1=str(error)))


def main(argv=None):
    parser = ArgumentParser(description=_('Nasazení z hotového disku nebo instalačního ISO přes jednotný adaptér.'))
    parser.add_argument("action", choices=("probe", "create", "install", "provision"))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    manifest = json.loads(Path(os.environ["NIXBERRY_PORTABLE_MANIFEST"]).read_text())
    target = manifest["target"]
    validate(target["spec"], target["backend"])
    if args.action == "probe":
        if args.apply:
            parser.error(_('probe je pouze čtení.'))
        print(json.dumps(snapshot(target), ensure_ascii=False, indent=2))
        return 0
    if not args.apply:
        print(json.dumps({"action": args.action, "target": target, "method": target.get("deploymentMethod", "image"), "imagePath": manifest.get("imagePath"), "isoPath": manifest.get("isoPath"),
                          "systemPath": manifest["systemPath"], "readOnly": True}, ensure_ascii=False, indent=2))
        if args.action != "install":
            # Skutečný průzkum bez zápisu; neexistující hostitel vrátí konkrétní chybu.
            print(json.dumps(rpc(target, "plan", imageBytes=image_file(manifest).stat().st_size), indent=2))
        else:
            print(json.dumps(snapshot(target), indent=2))
        if target.get("deploymentMethod", "image") == "iso":
            info(_('Metoda iso vytvoří novou VM s prázdným diskem; install ověří instalační prostředí, zapíše systém a odpojí ISO.'))
            if args.action == "install":
                install_iso(target, manifest, apply=False)
        else:
            info(_('S --apply se importuje hotový disk, neformátuje se existující VM. install spustí a ověří hotový systém.'))
        return 0
    if args.action in ("create", "provision"):
        create(target, manifest)
    if args.action in ("install", "provision"):
        if target.get("deploymentMethod", "image") == "iso":
            install_iso(target, manifest, apply=True)
        else:
            start_and_verify(target, manifest)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        report_error("{error}", error=error)
        raise SystemExit(1)
