#!/usr/bin/env bash
# Repeat checks in the same SSH session immediately before writing the virtual disk.
set -euo pipefail
# Transferred scripts include the helper; direct execution uses the working copy.
if ! declare -F nb_info >/dev/null; then
  source "${NIXBERRY_MESSAGES_SH:-${BASH_SOURCE[0]%/*}/../deployment/messages.sh}"
fi
mode=$1
expected_uuid=$2
expected_system=$3
expected_disko=$4
expected_bytes=$5
fail() { nb_error "$@"; exit 1; }

[[ "$(cat /sys/class/dmi/id/product_uuid)" == "$expected_uuid" ]] || fail 'Guest UUID does not match.'
[[ "$(hostname)" == nixberry-installer ]] || fail 'The guest is not an installation environment.'
[[ "$(findmnt -n -o FSTYPE /)" == tmpfs ]] || fail 'The root filesystem is not the installation tmpfs.'
test -d /sys/firmware/efi || fail 'The guest did not boot through UEFI.'
mountpoint -q /iso || fail 'The installation ISO is not attached.'
[[ "$(jq -er .profile /etc/nixberry-installation.json)" == nixberry-vm ]] || fail 'Profile does not match.'
[[ "$(jq -er .disk /etc/nixberry-installation.json)" == /dev/vda ]] || fail 'Disk does not match.'
[[ "$(jq -er .system /etc/nixberry-installation.json)" == "$expected_system" ]] || fail 'System does not match.'
[[ "$(jq -er .disko /etc/nixberry-installation.json)" == "$expected_disko" ]] || fail 'Disko does not match.'
[[ "$(blockdev --getsize64 /dev/vda)" == "$expected_bytes" ]] || fail 'Disk size does not match.'
[[ "$(lsblk -n -o NAME /dev/vda | wc -l)" -eq 1 ]] || fail 'The disk already contains partitions.'
[[ -z "$(wipefs --no-act --noheadings /dev/vda)" ]] || fail 'The disk already contains signatures.'
if findmnt --mountpoint /mnt >/dev/null; then fail '/mnt is already mounted.'; fi
nix --extra-experimental-features nix-command path-info --offline "$expected_system" "$expected_disko"
nb_info 'Verified: installation VM, UUID, UEFI, pinned outputs and blank /dev/vda.'
[[ "$mode" == apply ]] || exit 0

"$expected_disko"
nixos-install --root /mnt --system "$expected_system" --no-root-passwd --no-channel-copy \
  --option substituters "" --option extra-substituters ""
[[ "$(readlink -f /mnt/nix/var/nix/profiles/system)" == "/mnt$expected_system" ]] \
  || [[ "$(readlink -f /mnt/nix/var/nix/profiles/system)" == "$expected_system" ]] \
  || fail 'The system profile does not match after installation.'
test -f /mnt/boot/EFI/BOOT/BOOTX64.EFI || fail 'The EFI bootloader is missing.'
sync
nb_success 'Installation completed. The VM can be shut down gracefully and the ISO detached.'
