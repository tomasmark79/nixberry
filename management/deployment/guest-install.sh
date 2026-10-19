#!/usr/bin/env bash
# Shared installation checks for Proxmox, VirtualBox and VMware.
set -euo pipefail
mode=$1
expected_uuid=$2
expected_system=$3
expected_disko=$4
expected_bytes=$5
expected_profile=$6
disk=$7
fail() { nb_error "$@"; exit 1; }
[[ "$mode" == apply || "$mode" == dry-run ]] || fail 'Invalid installation mode.'
[[ "$disk" == /dev/vda || "$disk" == /dev/sda ]] || fail 'Disallowed target disk.'
[[ "$(hostname)" == nixberry-installer ]] || fail 'The guest is not an installation environment.'
[[ "$(cat /sys/class/dmi/id/product_uuid | tr '[:upper:]' '[:lower:]')" == "$expected_uuid" ]] || fail 'Installation VM UUID does not match.'
[[ -d /sys/firmware/efi ]] || fail 'The installation VM did not boot through UEFI.'
mountpoint -q /iso || fail 'The installation ISO is not attached.'
[[ "$(jq -er .profile /etc/nixberry-installation.json)" == "$expected_profile" ]] || fail 'Installer profile does not match.'
[[ "$(jq -er .disk /etc/nixberry-installation.json)" == "$disk" ]] || fail 'Installer target disk does not match.'
[[ "$(jq -er .system /etc/nixberry-installation.json)" == "$expected_system" ]] || fail 'Installer target system does not match.'
[[ "$(jq -er .disko /etc/nixberry-installation.json)" == "$expected_disko" ]] || fail 'Installer disko does not match.'
[[ "$(blockdev --getsize64 "$disk")" == "$expected_bytes" ]] || fail 'Target disk size does not match.'
[[ "$(lsblk -n -o NAME "$disk" | wc -l)" -eq 1 ]] || fail 'The disk already contains partitions.'
[[ -z "$(wipefs --no-act --noheadings "$disk")" ]] || fail 'The disk already contains signatures.'
if findmnt --mountpoint /mnt >/dev/null; then fail '/mnt is already mounted.'; fi
nix --extra-experimental-features nix-command path-info --offline "$expected_system" "$expected_disko"
nb_info 'Verified installation ISO, UUID, profile, UEFI, target system and blank disk.'
[[ "$mode" == apply ]] || exit 0
"$expected_disko"
nixos-install --root /mnt --system "$expected_system" --no-root-passwd --no-channel-copy \
  --option substituters '' --option extra-substituters ''
[[ "$(readlink -f /mnt/nix/var/nix/profiles/system)" == "/mnt$expected_system" ]] \
  || [[ "$(readlink -f /mnt/nix/var/nix/profiles/system)" == "$expected_system" ]] \
  || fail 'The system profile does not match after installation.'
test -f /mnt/boot/EFI/BOOT/BOOTX64.EFI || fail 'The EFI bootloader is missing.'
sync
nb_success 'The system is installed on disk, ready for shutdown and ISO detachment.'
