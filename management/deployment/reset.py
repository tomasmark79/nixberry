"""Chráněné odstranění deklarované VM bez sestavování systému a instalačních médií."""

import base64
import json
import os
from pathlib import Path
import runpy
import subprocess

from messages import _, ArgumentParser, info, success, localized_script, error, forward_stderr
from portable import rpc


def hyperv(target, apply):
    spec = target["spec"]
    runpy.run_path(os.environ["NIXBERRY_HYPERV_POLICY_MODULE"])["validate"](spec)
    api = runpy.run_path(os.environ["NIXBERRY_HYPERV_PROBE_MODULE"])
    request = base64.b64encode(json.dumps({"spec": spec, "apply": apply,
        "allowReset": target.get("allowReset")}).encode()).decode()
    script = "$Request = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + request + "')) | ConvertFrom-Json\n"
    script += localized_script(os.environ["NIXBERRY_HYPERV_RESET"])
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
        "--", api["destination"](spec), api["powershell_command"]()],
        input=api["powershell_input"](script), text=True, encoding="utf-8", capture_output=True, timeout=240)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or _('Odstranění Hyper-V VM se nepodařilo potvrdit; zkontroluj status a stav úložiště.'))
    forward_stderr(result.stderr)
    return json.loads(result.stdout.lstrip("\ufeff"))


def main(argv=None):
    parser = ArgumentParser(description=_(__doc__))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    target = json.loads(Path(os.environ["NIXBERRY_RESET_TARGET"]).read_text())
    if target.get("allowReset") is not True or target.get("offline"):
        raise RuntimeError(_('Reset vyžaduje allowReset = true; u této instance a deklarovaného hostitele hypervizoru.'))
    info(_('Ověřuji vlastníka, disky, snapshoty a stav VM před odstraněním…'))
    if args.apply:
        info(_('Po ověření korektně vypnu běžící VM a odstraním pouze její vlastní prostředky.'))
    result = hyperv(target, args.apply) if target["backend"] == "hyperv" else rpc(
        target, "reset", allowReset=True, apply=args.apply, timeout=300)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.apply:
        success(_('Odstranění ověřené VM dokončeno. Instanční deklarace zůstala zachovaná.'))
    else:
        info(_('Plán odstranění ověřený; nic se nevypínalo ani nemazalo. S --apply se VM korektně vypne a po opětovné kontrole odstraní.'))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as failure:
        error(_('{error} Po přerušení nebo částečném odstranění nejprve zkontroluj status a úložiště; operaci automaticky neopakuji.'), error=failure)
        raise SystemExit(1)
