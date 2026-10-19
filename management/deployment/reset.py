"Safely remove a declared VM without building the system or installation media."

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
        raise RuntimeError(result.stderr.strip() or _("Could not confirm Hyper-V VM removal; check status and storage state."))
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
        raise RuntimeError(_("Reset requires allowReset = true; for this instance and a declared hypervisor host."))
    info(_("Checking owner, disks, snapshots and VM state before removal…"))
    if args.apply:
        info(_("After verification, the running VM will be shut down gracefully and only its own resources removed."))
    result = hyperv(target, args.apply) if target["backend"] == "hyperv" else rpc(
        target, "reset", allowReset=True, apply=args.apply, timeout=300)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.apply:
        success(_("Verified VM removal completed. The instance declaration was preserved."))
    else:
        info(_("Removal plan verified; nothing was shut down or deleted. With --apply, the VM is gracefully shut down and removed after revalidation."))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as failure:
        error(_("{error} After interruption or partial removal, inspect status and storage first; the operation will not be retried automatically."), error=failure)
        raise SystemExit(1)
