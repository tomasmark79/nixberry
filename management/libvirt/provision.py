#!/usr/bin/env python3
"""Vytvoření a instalace testovací VM jedním příkazem; bez --apply pouze plán."""

from messages import _, ArgumentParser, info, success, error as report_error

import argparse
import os
import subprocess
import sys
import time


def main():
    parser = ArgumentParser(description=_('Vytvoření a instalace testovací VM jedním příkazem; bez --apply pouze plán.'))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    create = os.environ["NIXBERRY_CREATE"]
    install = os.environ["NIXBERRY_INSTALL"]
    if not args.apply:
        subprocess.run([create, "--dry-run"], check=True)
        info(_('S --apply: vytvoření VM a dokončení zvolené metody nasazení včetně ověření startu z disku.'))
        return
    subprocess.run([create, "--apply"], check=True)
    info(_('Ověřuji připravenost VM pro dokončení zvolené metody nasazení.'))
    deadline = time.monotonic() + 180
    while True:
        result = subprocess.run([install, "--dry-run"], text=True,
                                capture_output=True, timeout=30, check=False)
        if result.returncode == 0:
            print(result.stdout, end="", flush=True)
            print(result.stderr, file=sys.stderr, end="", flush=True)
            break
        if time.monotonic() >= deadline:
            raise RuntimeError(_('Instalační prostředí není připravené: {value1}').format(value1=result.stderr))
        time.sleep(2)
    subprocess.run([install, "--apply"], check=True)
    success(_('Celé vytvoření dokončené a ověřené: VM běží z deklarovaného systému na disku.'))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.SubprocessError, OSError) as error:
        report_error("{error}", error=error)
        raise SystemExit(1)
