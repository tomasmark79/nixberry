#!/usr/bin/env python3
"Discover Windows and Hyper-V over SSH without changing the host or VMs."

from messages import _, ArgumentParser, localized_script, forward_stderr, error as report_error

import argparse
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess

INPUT_END = "__NIXBERRY_INPUT_END__"


def powershell_input(script):
    if INPUT_END in script.splitlines():
        raise ValueError(_("The script contains a reserved input terminator."))
    return script.rstrip("\r\n") + "\n" + INPUT_END + "\n"

def destination(spec):
    address = ipaddress.IPv4Address(spec["sshHost"])
    if str(address) == "192.168.79.1" or not any(
        address in ipaddress.IPv4Network(network)
        for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    ):
        raise ValueError(_("Disallowed Windows host."))
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", spec["sshUser"]):
        raise ValueError(_("Disallowed SSH account."))
    return spec["sshUser"] + "@" + str(address)


def powershell_command():
    # A short bootstrap bypasses cmd.exe length limits; read the script from
    # UTF-8 stdin. An explicit marker avoids withheld EOF in Windows SSH.
    code = (
        "$ErrorActionPreference='Stop';"
        "$ProgressPreference='SilentlyContinue';"
        "[Console]::InputEncoding=[Text.UTF8Encoding]::new($false);"
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
        "$lines=[Collections.Generic.List[string]]::new();"
        "while($null -ne ($line=[Console]::ReadLine())){"
        f"if($line -eq '{INPUT_END}'){{break}};"
        "$lines.Add($line)};"
        'try { & ([ScriptBlock]::Create(($lines -join "`n"))) }'
        "catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }"
    )
    encoded = base64.b64encode(code.encode("utf-16le")).decode("ascii")
    return "powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand " + encoded


def main():
    parser = ArgumentParser(description=_("Discover Windows and Hyper-V over SSH without changing the host or VMs."))
    parser.add_argument("--status-only", action="store_true", help=_("Only report selected VM status without inspecting disks or snapshots"))
    parser.add_argument("--vm-name", action="append", help=_("VM name for fast status; repeatable"))
    args = parser.parse_args()
    spec = json.loads(Path(os.environ["NIXBERRY_HYPERV_SPEC"]).read_text())
    if args.status_only:
        names = base64.b64encode(json.dumps(args.vm_name or [spec["name"]]).encode()).decode()
        script = "$names = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + names + "')) | ConvertFrom-Json\n"
        script += localized_script(os.environ["NIXBERRY_HYPERV_STATUS"])
    else:
        script = localized_script(os.environ["NIXBERRY_HYPERV_PROBE"])
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
         "--", destination(spec), powershell_command()],
        input=powershell_input(script),
        text=True, encoding="utf-8", capture_output=True, timeout=20 if args.status_only else 60,
    )
    if result.returncode:
        raise RuntimeError((result.stderr.strip() or _("Windows discovery failed.")))
    forward_stderr(result.stderr)
    data = json.loads(result.stdout.lstrip("\ufeff"))
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except subprocess.TimeoutExpired as failure:
        report_error(_("Windows and Hyper-V discovery did not finish within {seconds} seconds; check host availability."), seconds=failure.timeout)
        raise SystemExit(1)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report_error("{error}", error=error)
        raise SystemExit(1)
