#!/usr/bin/env python3
"""Kontrola formátů, GPT, ext4 a UEFI souborů hotových disků bez jejich startu."""

from messages import _, ArgumentParser, error as report_error

import argparse
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import uuid


def run(command):
    result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=180)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout))
    return result.stdout


def check(path):
    path = Path(path).resolve(strict=True)
    info = json.loads(run(["qemu-img", "info", "--output=json", str(path)]))
    expected = {".qcow2": "qcow2", ".vdi": "vdi", ".vmdk": "vmdk", ".vhdx": "vhdx"}[path.suffix]
    if info["format"] != expected or info["virtual-size"] < 8 * 1024 ** 3 or info.get("backing-filename"):
        raise RuntimeError(_('Neplatný formát, kapacita nebo neočekávaný backing file.'))
    if expected in ("qcow2", "vmdk"):
        run(["qemu-img", "check", "-f", expected, str(path)])
    with tempfile.TemporaryDirectory(prefix="nixberry-image-check-") as directory:
        root = Path(directory)

        def extract(name, first, count):
            output = root / name
            # qemu-img dd omezuje count vůči začátku vstupu, před odečtením skip.
            run(["qemu-img", "dd", "-f", expected, "if=" + str(path), "of=" + str(output),
                 "bs=512", "skip=" + str(first), "count=" + str(first + count)])
            if output.stat().st_size != count * 512:
                raise RuntimeError(_('Neúplná extrakce {name}: {value2} bajtů místo {value3}.').format(name=name, value2=output.stat().st_size, value3=count * 512))
            return output

        header = extract("gpt", 0, 34).read_bytes()
        if header[512:520] != b"EFI PART":
            raise RuntimeError(_('Chybí GPT.'))
        table, count, stride = struct.unpack_from("<QII", header, 512 + 72)
        if table != 2 or count > 128 or stride != 128:
            raise RuntimeError(_('Neočekávané rozložení GPT.'))
        partitions = []
        for index in range(count):
            offset = table * 512 + index * stride
            kind = header[offset:offset + 16]
            if kind == bytes(16):
                continue
            first, last = struct.unpack_from("<QQ", header, offset + 32)
            if first < 34 or last < first or (last + 1) * 512 > info["virtual-size"]:
                raise RuntimeError(_('Oddíl přesahuje disk.'))
            partitions.append((str(uuid.UUID(bytes_le=kind)), first, last))
        esp = [p for p in partitions if p[0] == "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"]
        linux = [p for p in partitions if p[0] == "0fc63daf-8483-4772-8e79-3d69d8477de4"]
        if len(esp) != 1 or len(linux) != 1:
            raise RuntimeError(_('Očekávám právě jeden ESP a jeden linuxový oddíl.'))
        if esp[0][2] - esp[0][1] + 1 > 2 * 1024 * 1024:
            raise RuntimeError(_('Neočekávaně velký ESP.'))
        boot = extract("boot.raw", esp[0][1], esp[0][2] - esp[0][1] + 1)
        run(["mdir", "-b", "-i", str(boot), "::/EFI/BOOT/BOOTX64.EFI"])
        entries = run(["mtype", "-i", str(boot), "::/loader/entries/*.conf"])
        if "linux" not in entries or "initrd" not in entries or "init=/nix/store/" not in entries:
            raise RuntimeError(_('Bootovací záznam neodkazuje na kernel, initrd a systém.'))
        superblock = extract("root-header", linux[0][1], 3).read_bytes()
        if superblock[1080:1082] != b"\x53\xef":
            raise RuntimeError(_('Kořenový oddíl není ext4.'))
    return {"path": str(path), "format": expected, "capacityBytes": info["virtual-size"],
            "gpt": True, "ext4": True, "efiFallback": True, "bootEntry": True,
            "note": _('Kontrola obsahu není důkaz startu na hypervizoru.')}


def main():
    parser = ArgumentParser(description=_('Kontrola formátů, GPT, ext4 a UEFI souborů hotových disků bez jejich startu.'))
    parser.add_argument("images", nargs="+")
    args = parser.parse_args()
    for image in args.images:
        print(json.dumps(check(image), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, struct.error, subprocess.SubprocessError) as error:
        report_error("{error}", error=error)
        raise SystemExit(1)
