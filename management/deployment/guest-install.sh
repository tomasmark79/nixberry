#!/usr/bin/env bash
# Instalační kontrola je společná pro Proxmox, VirtualBox a VMware.
set -euo pipefail
mode=$1
expected_uuid=$2
expected_system=$3
expected_disko=$4
expected_bytes=$5
expected_profile=$6
disk=$7
fail() { nb_error "$@"; exit 1; }
[[ "$mode" == apply || "$mode" == dry-run ]] || fail 'Neplatný režim instalace.'
[[ "$disk" == /dev/vda || "$disk" == /dev/sda ]] || fail 'Nepovolený cílový disk.'
[[ "$(hostname)" == nixberry-installer ]] || fail 'Host není instalační prostředí.'
[[ "$(cat /sys/class/dmi/id/product_uuid | tr '[:upper:]' '[:lower:]')" == "$expected_uuid" ]] || fail 'Nesouhlasí UUID instalační VM.'
[[ -d /sys/firmware/efi ]] || fail 'Instalační VM neběží přes UEFI.'
mountpoint -q /iso || fail 'Instalační ISO není připojené.'
[[ "$(jq -er .profile /etc/nixberry-installation.json)" == "$expected_profile" ]] || fail 'Nesouhlasí profil instalátoru.'
[[ "$(jq -er .disk /etc/nixberry-installation.json)" == "$disk" ]] || fail 'Nesouhlasí cílový disk instalátoru.'
[[ "$(jq -er .system /etc/nixberry-installation.json)" == "$expected_system" ]] || fail 'Nesouhlasí cílový systém instalátoru.'
[[ "$(jq -er .disko /etc/nixberry-installation.json)" == "$expected_disko" ]] || fail 'Nesouhlasí disko instalátoru.'
[[ "$(blockdev --getsize64 "$disk")" == "$expected_bytes" ]] || fail 'Nesouhlasí velikost cílového disku.'
[[ "$(lsblk -n -o NAME "$disk" | wc -l)" -eq 1 ]] || fail 'Disk už obsahuje oddíly.'
[[ -z "$(wipefs --no-act --noheadings "$disk")" ]] || fail 'Disk už obsahuje signatury.'
if findmnt --mountpoint /mnt >/dev/null; then fail '/mnt je již připojené.'; fi
nix --extra-experimental-features nix-command path-info --offline "$expected_system" "$expected_disko"
nb_info 'Ověřeno instalační ISO, UUID, profil, UEFI, cílový systém a prázdný disk.'
[[ "$mode" == apply ]] || exit 0
"$expected_disko"
nixos-install --root /mnt --system "$expected_system" --no-root-passwd --no-channel-copy \
  --option substituters '' --option extra-substituters ''
[[ "$(readlink -f /mnt/nix/var/nix/profiles/system)" == "/mnt$expected_system" ]] \
  || [[ "$(readlink -f /mnt/nix/var/nix/profiles/system)" == "$expected_system" ]] \
  || fail 'Po instalaci nesouhlasí systémový profil.'
test -f /mnt/boot/EFI/BOOT/BOOTX64.EFI || fail 'Chybí EFI zavaděč.'
sync
nb_success 'Systém je nainstalovaný na disku; připravený k vypnutí a odpojení ISO.'
