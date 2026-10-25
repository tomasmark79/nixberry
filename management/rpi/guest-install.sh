#!/usr/bin/env bash
# Recheck the physical machine and disk immediately before installation.
set -euo pipefail
# Transferred scripts include the helper; direct execution uses the working copy.
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
security=${11:-none}
initrd_host_key=${12:-}
fail() { nb_error "$@"; exit 1; }

[[ "$(tr -d '\0' </proc/device-tree/serial-number)" == "$expected_serial" ]] || fail 'RPi does not match.'
[[ "$(uname -m)" == aarch64 ]] || fail 'The guest is not ARM64.'
test -f /etc/nixberry-rpi-targets.json || fail 'The guest is not running the prepared installation environment.'
[[ -b "$device" ]] || fail 'The target is not a block device.'
[[ "$(lsblk -dn -o SERIAL "$device" | xargs)" == "$disk_serial" ]] || fail 'Disk serial number does not match.'
[[ "$(blockdev --getsize64 "$device")" -ge "$minimum_bytes" ]] || fail 'The disk is too small.'
[[ -z "$(lsblk -n -o MOUNTPOINTS "$device" | tr -d '[:space:]')" ]] || fail 'The disk is mounted.'
if [[ "$mode" == erase-existing ]]; then
  while read -r type; do
    [[ "$type" == part ]] || fail 'The disk has child devices other than regular partitions.'
  done < <(lsblk -n -o TYPE "$device" | tail -n +2)
else
  [[ "$(lsblk -n -o PATH "$device" | wc -l)" -eq 1 ]] || fail 'The disk already contains child devices.'
  [[ -z "$(wipefs --no-act --noheadings "$device")" ]] || fail 'The disk already contains signatures.'
fi
for label in "$boot_label" "$root_label"; do
  if [[ -e "/dev/disk/by-partlabel/$label" ]]; then
    [[ "$mode" == erase-existing ]] || fail 'Another disk already uses the partition label.'
    [[ "/dev/$(lsblk -n -o PKNAME "/dev/disk/by-partlabel/$label")" == "$(readlink -e "$device")" ]] \
      || fail 'Another disk uses the partition label.'
  fi
done
findmnt --mountpoint /mnt >/dev/null && fail '/mnt is already mounted.'
"$jq" -e --arg system "$system" --arg disko "$disko" --arg security "$security" --arg hostKey "$initrd_host_key" \
  'any(.[]; .systemPath == $system and .diskoPath == $disko and (.security.mode // "none") == $security and ($security == "none" or .security.initrdHostKey == $hostKey))' \
  /etc/nixberry-rpi-targets.json >/dev/null || fail 'Pinned installation manifest does not match.'
nix --extra-experimental-features nix-command path-info --offline "$system" "$disko"
if [[ "$security" == luks ]]; then
  [[ "$(stat -f -c %T /run)" == tmpfs ]] || fail 'RPi installation secrets require /run on tmpfs.'
  [[ -s /run/nixberry/luks-password && -s /run/nixberry/ssh_host_ed25519_key ]] \
    || fail 'Runtime LUKS and initrd SSH secrets are missing; the disk was not changed.'
  actual_key=$(ssh-keygen -y -P '' -f /run/nixberry/ssh_host_ed25519_key)
  [[ "${actual_key%% *} $(awk '{print $2}' <<< "$actual_key")" == "$(awk '{print $1, $2}' <<< "$initrd_host_key")" ]] \
    || fail 'The private initrd SSH key does not match the trusted RPi public key.'
elif [[ "$security" != none ]]; then
  fail 'Unknown RPi security mode.'
fi
# Normal diskoScript preserves existing filesystems. Authorized clean recovery
# first removes signatures and the partition table from the target SSD.
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
if [[ "$security" == luks ]]; then
  [[ "$(readlink -f "$(findmnt -n -o SOURCE /mnt)")" == "$(readlink -f /dev/mapper/nixberry-root)" ]] \
    || fail 'The mounted RPi root is not the expected encrypted mapping.'
  LC_ALL=C cryptsetup status nixberry-root | grep -Eq '^[[:space:]]*type:[[:space:]]*LUKS2$' \
    || fail 'The mounted RPi root is not LUKS2.'
  backing=$(LC_ALL=C cryptsetup status nixberry-root | sed -n 's/^[[:space:]]*device:[[:space:]]*//p')
  [[ "$(readlink -e "$backing")" == "$(readlink -e "/dev/disk/by-partlabel/$root_label")" &&
     "/dev/$(lsblk -ndo PKNAME "$backing")" == "$(readlink -e "$device")" ]] \
    || fail 'The encrypted mapping does not belong to the verified RPi disk.'
  install -d -m 0700 /mnt/etc/secrets/initrd
  install -m 0600 /run/nixberry/ssh_host_ed25519_key /mnt/etc/secrets/initrd/ssh_host_ed25519_key
fi
nixos-install --root /mnt --system "$system" --no-root-passwd --no-channel-copy \
  --option substituters "" --option extra-substituters ""
test -d /mnt/boot/rpi-generations || fail 'Generated RPi boot files are missing.'
sync
nb_success 'Installation completed. The RPi remains in the installation environment; reboot and SD removal are separate steps.'
