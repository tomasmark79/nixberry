#!/usr/bin/env bash
# Kontroly proti záznamu Hyper-V se opakují těsně před zápisem do nového VHDX.
set -euo pipefail
# Při přenosu je helper přiložený; přímé spuštění používá zdroj z pracovní kopie.
if ! declare -F nb_info >/dev/null; then
  source "${NIXBERRY_MESSAGES_SH:-${BASH_SOURCE[0]%/*}/../deployment/messages.sh}"
fi
mode=$1
expected_uuid=$2
expected_system=$3
expected_disko=$4
expected_bytes=$5
fail() { nb_error "$@"; exit 1; }
[[ "$(uname -m)" == x86_64 ]] || fail 'Host není x86_64.'
[[ "$(cat /sys/class/dmi/id/product_uuid | tr '[:upper:]' '[:lower:]')" == "$expected_uuid" ]] || fail 'Nesouhlasí UUID VM.'
[[ "$(hostname)" == nixberry-installer ]] || fail 'Host není instalační prostředí.'
[[ "$(findmnt -n -o FSTYPE /)" == tmpfs ]] || fail 'Kořen není instalační tmpfs.'
test -d /sys/firmware/efi || fail 'Host nenaběhl přes UEFI.'
mountpoint -q /iso || fail 'ISO není připojené.'
[[ "$(jq -er .profile /etc/nixberry-installation.json)" == nixberry-hyperv ]] || fail 'Nesouhlasí profil.'
[[ "$(jq -er .disk /etc/nixberry-installation.json)" == /dev/sda ]] || fail 'Nesouhlasí disk.'
[[ "$(jq -er .system /etc/nixberry-installation.json)" == "$expected_system" ]] || fail 'Nesouhlasí systém.'
[[ "$(jq -er .disko /etc/nixberry-installation.json)" == "$expected_disko" ]] || fail 'Nesouhlasí disko.'
[[ "$(blockdev --getsize64 /dev/sda)" == "$expected_bytes" ]] || fail 'Nesouhlasí kapacita disku.'
[[ "$(lsblk -nr -o PATH /dev/sda | wc -l)" -eq 1 ]] || fail 'Disk už obsahuje oddíly.'
[[ -z "$(lsblk -nr -o MOUNTPOINTS /dev/sda | tr -d '[:space:]')" ]] || fail 'Disk je připojený.'
[[ -z "$(wipefs --no-act --noheadings /dev/sda)" ]] || fail 'Disk obsahuje signatury.'
findmnt --mountpoint /mnt >/dev/null && fail '/mnt je připojené.'
nix --extra-experimental-features nix-command path-info --offline "$expected_system" "$expected_disko"
nb_info 'Ověřeno: Hyper-V UUID, UEFI, manifest a prázdný disk.'
[[ "$mode" == apply ]] || exit 0
"$expected_disko"
nixos-install --root /mnt --system "$expected_system" --no-root-passwd --no-channel-copy \
  --option substituters "" --option extra-substituters ""
profile=$(readlink -f /mnt/nix/var/nix/profiles/system)
[[ "$profile" == "$expected_system" || "$profile" == "/mnt$expected_system" ]] || fail 'Nesouhlasí instalovaný profil.'
test -s /mnt/boot/EFI/BOOT/BOOTX64.EFI || fail 'Chybí EFI zavaděč.'
sync
nb_success 'Instalace Hyper-V dokončená.'
