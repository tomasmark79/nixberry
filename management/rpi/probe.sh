#!/usr/bin/env bash
# Read-only; delimiters allow JSON processing without extra guest packages.
set -euo pipefail
uname -m
printf '\036'
tr -d '\0' < /proc/device-tree/model
printf '\036'
tr -d '\0' < /proc/device-tree/serial-number
printf '\036'
hostname
printf '\036'
findmnt -n -o SOURCE /
printf '\036'
ip -j link
printf '\036'
lsblk --tree -J -b -o PATH,TYPE,SIZE,SERIAL,PARTLABEL,MOUNTPOINTS
printf '\036'
readlink -e "$1" || true
printf '\036'
cat /etc/nixberry-rpi-targets.json 2>/dev/null || printf '{}'
printf '\036'
if [[ -b "$1" ]]; then
  wipefs --no-act --json "$1"
else
  printf '{}'
fi
