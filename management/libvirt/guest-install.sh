#!/usr/bin/env bash
# Kontroly se opakují ve stejné SSH relaci těsně před zásahem do virtuálního disku.
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

[[ "$(cat /sys/class/dmi/id/product_uuid)" == "$expected_uuid" ]] || fail 'Nesouhlasí UUID hosta.'
[[ "$(hostname)" == nixberry-installer ]] || fail 'Host není instalační prostředí.'
[[ "$(findmnt -n -o FSTYPE /)" == tmpfs ]] || fail 'Kořen není instalační tmpfs.'
test -d /sys/firmware/efi || fail 'Host nenaběhl přes UEFI.'
mountpoint -q /iso || fail 'Instalační ISO není připojené.'
[[ "$(jq -er .profile /etc/nixberry-installation.json)" == nixberry-vm ]] || fail 'Nesouhlasí profil.'
[[ "$(jq -er .disk /etc/nixberry-installation.json)" == /dev/vda ]] || fail 'Nesouhlasí disk.'
[[ "$(jq -er .system /etc/nixberry-installation.json)" == "$expected_system" ]] || fail 'Nesouhlasí systém.'
[[ "$(jq -er .disko /etc/nixberry-installation.json)" == "$expected_disko" ]] || fail 'Nesouhlasí disko.'
[[ "$(blockdev --getsize64 /dev/vda)" == "$expected_bytes" ]] || fail 'Nesouhlasí velikost disku.'
[[ "$(lsblk -n -o NAME /dev/vda | wc -l)" -eq 1 ]] || fail 'Disk už obsahuje oddíly.'
[[ -z "$(wipefs --no-act --noheadings /dev/vda)" ]] || fail 'Disk už obsahuje signatury.'
if findmnt --mountpoint /mnt >/dev/null; then fail '/mnt je již připojené.'; fi
nix --extra-experimental-features nix-command path-info --offline "$expected_system" "$expected_disko"
nb_info 'Ověřeno: instalační VM, UUID, UEFI, připnuté výstupy a prázdný /dev/vda.'
[[ "$mode" == apply ]] || exit 0

"$expected_disko"
nixos-install --root /mnt --system "$expected_system" --no-root-passwd --no-channel-copy \
  --option substituters "" --option extra-substituters ""
[[ "$(readlink -f /mnt/nix/var/nix/profiles/system)" == "/mnt$expected_system" ]] \
  || [[ "$(readlink -f /mnt/nix/var/nix/profiles/system)" == "$expected_system" ]] \
  || fail 'Po instalaci nesouhlasí systémový profil.'
test -f /mnt/boot/EFI/BOOT/BOOTX64.EFI || fail 'Chybí EFI zavaděč.'
sync
nb_success 'Instalace dokončená. VM lze korektně vypnout a odpojit ISO.'
