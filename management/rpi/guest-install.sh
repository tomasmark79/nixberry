#!/usr/bin/env bash
# Znovu kontrolujeme fyzický stroj i disk těsně před instalací.
set -euo pipefail
# Při přenosu je helper přiložený; přímé spuštění používá zdroj z pracovní kopie.
if ! declare -F nb_info >/dev/null; then
  source "${NIXBERRY_MESSAGES_SH:-${BASH_SOURCE[0]%/*}/../deployment/messages.sh}"
fi
expected_serial=$1
device=$2
disk_serial=$3
minimum_bytes=$4
system=$5
disko=$6
boot_label=$7
root_label=$8
jq=$9
mode=${10}
fail() { nb_error "$@"; exit 1; }

[[ "$(tr -d '\0' </proc/device-tree/serial-number)" == "$expected_serial" ]] || fail 'Nesouhlasí RPi.'
[[ "$(uname -m)" == aarch64 ]] || fail 'Host není ARM64.'
test -f /etc/nixberry-rpi-targets.json || fail 'Host neběží z připraveného instalačního prostředí.'
[[ -b "$device" ]] || fail 'Cíl není blokové zařízení.'
[[ "$(lsblk -dn -o SERIAL "$device" | xargs)" == "$disk_serial" ]] || fail 'Nesouhlasí sériové číslo disku.'
[[ "$(blockdev --getsize64 "$device")" -ge "$minimum_bytes" ]] || fail 'Disk je příliš malý.'
[[ -z "$(lsblk -n -o MOUNTPOINTS "$device" | tr -d '[:space:]')" ]] || fail 'Disk je připojený.'
if [[ "$mode" == erase-existing ]]; then
  while read -r type; do
    [[ "$type" == part ]] || fail 'Disk má podřízená zařízení jiná než běžné oddíly.'
  done < <(lsblk -n -o TYPE "$device" | tail -n +2)
else
  [[ "$(lsblk -n -o PATH "$device" | wc -l)" -eq 1 ]] || fail 'Disk už obsahuje podřízená zařízení.'
  [[ -z "$(wipefs --no-act --noheadings "$device")" ]] || fail 'Disk už obsahuje signatury.'
fi
for label in "$boot_label" "$root_label"; do
  if [[ -e "/dev/disk/by-partlabel/$label" ]]; then
    [[ "$mode" == erase-existing ]] || fail 'Označení oddílu už používá jiný disk.'
    [[ "/dev/$(lsblk -n -o PKNAME "/dev/disk/by-partlabel/$label")" == "$(readlink -e "$device")" ]] \
      || fail 'Označení oddílu používá jiný disk.'
  fi
done
findmnt --mountpoint /mnt >/dev/null && fail '/mnt je již připojené.'
"$jq" -e --arg system "$system" --arg disko "$disko" \
  'any(.[]; .systemPath == $system and .diskoPath == $disko)' \
  /etc/nixberry-rpi-targets.json >/dev/null || fail 'Nesouhlasí připnutý instalační manifest.'
nix --extra-experimental-features nix-command path-info --offline "$system" "$disko"
# Běžný diskoScript existující filesystémy zachovává. Schválená čistá obnova
# proto nejprve odstraní jejich signatury i tabulku oddílů cílového SSD.
if [[ "$mode" == erase-existing ]]; then
  while read -r partition type; do
    if [[ "$type" == part ]]; then
      wipefs --all "$partition"
    fi
  done < <(lsblk -nr -o PATH,TYPE "$device")
  wipefs --all "$device"
  udevadm settle
fi
"$disko"
nixos-install --root /mnt --system "$system" --no-root-passwd --no-channel-copy \
  --option substituters "" --option extra-substituters ""
test -d /mnt/boot/rpi-generations || fail 'Chybí generované startovací soubory RPi.'
sync
nb_success 'Instalace dokončená. RPi zůstává v instalačním prostředí; restart a vyjmutí SD jsou samostatný krok.'
