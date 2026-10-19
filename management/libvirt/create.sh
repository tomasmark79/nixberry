#!/usr/bin/env bash
# Vytvoří pouze novou testovací VM. Bez --apply provádí výhradně kontroly.
set -euo pipefail
# Při přenosu je helper přiložený; přímé spuštění používá zdroj z pracovní kopie.
if ! declare -F nb_info >/dev/null; then
  source "${NIXBERRY_MESSAGES_SH:-${BASH_SOURCE[0]%/*}/../deployment/messages.sh}"
fi
export LC_ALL=C

mode=dry-run
case "${1:---dry-run}" in
  --dry-run) ;;
  --apply) mode=apply ;;
  --help)
    nb_help 'Použití: nixberry-vm-create [--dry-run|--apply]'
    nb_help 'Výchozí režim jen ověří plán. --apply vytvoří VM podle zvolené metody image nebo iso.'
    exit 0
    ;;
  *) nb_error 'Neznámá volba: %s' "$1"; exit 2 ;;
esac
if (( $# > 1 )); then
  nb_error 'Příkaz přijímá nejvýše jednu volbu.'
  exit 2
fi

value() { jq -er ".$1" "$NIXBERRY_MANIFEST"; }
uri=$(value uri)
name=$(value name)
uuid=$(value uuid)
pool=$(value storagePool)
directory=$(value storageDirectory)
volume=$(value volume)
disk=$(value diskPath)
size=$(value diskGiB)
network=$(value network)
mac=$(value mac)
method=$(value deploymentMethod)
iso=$(jq -r '.isoPath // empty' "$NIXBERRY_MANIFEST")
image=$(jq -r '.imagePath // empty' "$NIXBERRY_MANIFEST")
machine=$(value machine)
v() { virsh --connect "$uri" "$@"; }
fail() { nb_error "$@"; exit 1; }

# Záměrně nepřijímáme vzdálený host ani adresu serveru jako argument.
[[ "$uri" == qemu:///system ]] || fail 'Povolený je pouze místní qemu:///system.'
python3 "${NIXBERRY_POLICY:-$(dirname -- "${BASH_SOURCE[0]}")/policy.py}" "$NIXBERRY_MANIFEST"
[[ "$volume" == "$name.qcow2" ]] || fail 'Název disku neodpovídá testovací VM.'
if [[ "$method" == iso ]]; then
  [[ -f "$iso" ]] || fail 'Chybí sestavené instalační ISO: %s' "$iso"
else
  [[ "$method" == image && -f "$image" ]] || fail 'Chybí hotový systémový disk: %s' "$image"
fi
virt-xml-validate "$NIXBERRY_DOMAIN_XML" domain
v list --all >/dev/null

# Seznamy načítáme předem, aby chyba spojení nebyla zaměněna za volný název.
names=$(v list --all --name)
uuids=$(v list --all --uuid)
if grep -Fxq "$name" <<< "$names" || grep -Fxq "$uuid" <<< "$uuids"; then
  fail 'Testovací VM již existuje. Příkaz ji nepřepíše ani nerestartuje.'
fi
# Nejprve načteme celý výstup. grep -q v pipeline by mohl ukončit virsh přes SIGPIPE.
pool_info=$(v pool-info "$pool")
grep -Eq '^State: +running$' <<< "$pool_info" || fail 'Úložiště není aktivní.'
pool_xml=$(v pool-dumpxml "$pool")
pool_path=$(xmllint --xpath 'string(/pool/target/path)' - <<< "$pool_xml")
[[ "$pool_path" == "$directory" ]] || fail 'Cesta úložiště neodpovídá manifestu.'
volumes=$(v vol-list "$pool")
while read -r existing_volume _; do
  [[ "$existing_volume" != "$volume" ]] || fail 'Cílový disk již existuje v úložišti.'
done <<< "$volumes"
if [[ -e "$disk" || -L "$disk" ]]; then
  fail 'Cílový disk již existuje. Příkaz jej nepřepíše.'
fi
network_info=$(v net-info "$network")
grep -Eq '^Active: +yes$' <<< "$network_info" || fail 'Síť není aktivní.'
network_xml=$(v net-dumpxml "$network")
[[ $(xmllint --xpath 'string(/network/forward/@mode)' - <<< "$network_xml") == nat ]] \
  || fail 'Testovací síť musí používat NAT.'
v domcapabilities --virttype kvm --arch x86_64 --machine "$machine" >/dev/null

# Kontrola MAC zahrnuje i vypnuté stroje a statické rezervace DHCP.
while IFS= read -r domain; do
  [[ -n "$domain" ]] || continue
  domain_xml=$(v dumpxml "$domain")
  if grep -Fiq "$mac" <<< "$domain_xml"; then
    fail 'MAC je již použita strojem %s.' "$domain"
  fi
done <<< "$names"
if grep -Fiq "$mac" <<< "$network_xml"; then
  fail 'MAC má v síti již existující deklaraci.'
fi

nb_info 'Režim: %s; URI: %s; VM: %s' "$mode" "$uri" "$name"
nb_info 'Disk: %s (%s GiB); síť: %s' "$disk" "$size" "$network"
nb_info 'Metoda: %s; ISO: %s; diskový obraz: %s' "$method" "$iso" "$image"
if [[ "$mode" == dry-run ]]; then
  nb_success 'Kontroly prošly. Nebyl vytvořen disk ani VM.'
  exit 0
fi

# Při chybě nic automaticky nemažeme; vytvořené prostředky je nutné nejprve ověřit.
v vol-create-as "$pool" "$volume" "${size}G" --format qcow2
created_path=$(v vol-path "$volume" --pool "$pool")
[[ "$created_path" == "$disk" ]] || fail 'Libvirt vytvořil disk na jiné cestě: %s' "$created_path"
if [[ "$method" == image ]]; then
  v vol-upload "$volume" "$image" --pool "$pool"
fi
v define "$NIXBERRY_DOMAIN_XML"
if [[ "$method" == image ]]; then
  nb_success 'VM vytvořená s hotovým diskem; install ji spustí a ověří.'
  exit 0
fi
v start "$name"
nb_info 'Spuštěno instalační ISO. Cílový NixOS ještě není nainstalovaný.'
nb_info 'Konzole: virsh --connect %s console %s' "$uri" "$name"
nb_info 'Adresa: virsh --connect %s domifaddr %s --source lease' "$uri" "$name"
