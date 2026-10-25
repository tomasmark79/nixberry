#!/usr/bin/env bash
# Create only a new test VM. Without --apply, perform checks only.
set -euo pipefail
# Transferred scripts include the helper; direct execution uses the working copy.
if ! declare -F nb_info >/dev/null; then
  source "${NIXBERRY_MESSAGES_SH:-${BASH_SOURCE[0]%/*}/../deployment/messages.sh}"
fi
export LC_ALL=C

mode=dry-run
case "${1:---dry-run}" in
  --dry-run) ;;
  --apply) mode=apply ;;
  --help)
    nb_help 'Usage: nixberry-vm-create [--dry-run|--apply]'
    nb_help 'The default mode only verifies the plan. --apply creates a VM using the selected image or iso method.'
    exit 0
    ;;
  *) nb_error 'Unknown option: %s' "$1"; exit 2 ;;
esac
if (( $# > 1 )); then
  nb_error 'The command accepts at most one option.'
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

# Intentionally accept neither remote hosts nor server addresses as arguments.
[[ "$uri" == qemu:///system ]] || fail 'Only local qemu:///system is allowed.'
python3 "${NIXBERRY_POLICY:-$(dirname -- "${BASH_SOURCE[0]}")/policy.py}" "$NIXBERRY_MANIFEST"
[[ "$volume" == "$name.qcow2" ]] || fail 'The disk name does not match the test VM.'
if [[ "$method" == iso ]]; then
  [[ -f "$iso" ]] || fail 'The built installation ISO is missing: %s' "$iso"
else
  [[ "$method" == image && -f "$image" ]] || fail 'The prebuilt system disk is missing: %s' "$image"
fi
virt-xml-validate "$NIXBERRY_DOMAIN_XML" domain
v list --all >/dev/null

# Read listings first so connection failures cannot appear as available names.
names=$(v list --all --name)
uuids=$(v list --all --uuid)
if grep -Fxq "$name" <<< "$names" || grep -Fxq "$uuid" <<< "$uuids"; then
  fail 'The test VM already exists. It will not be overwritten or rebooted.'
fi
# Capture the full output first; grep -q in a pipeline could SIGPIPE virsh.
pool_info=$(v pool-info "$pool")
grep -Eq '^State: +running$' <<< "$pool_info" || fail 'The storage is inactive.'
pool_xml=$(v pool-dumpxml "$pool")
pool_path=$(xmllint --xpath 'string(/pool/target/path)' - <<< "$pool_xml")
[[ "$pool_path" == "$directory" ]] || fail 'The storage path does not match the manifest.'
volumes=$(v vol-list "$pool")
while read -r existing_volume _; do
  [[ "$existing_volume" != "$volume" ]] || fail 'The target disk already exists in storage.'
done <<< "$volumes"
if [[ -e "$disk" || -L "$disk" ]]; then
  fail 'The target disk already exists. It will not be overwritten.'
fi
adapters=$(jq -er '(.networkAdapters // [{mac: .mac, network: .network}])[] | [.mac, .network] | @tsv' "$NIXBERRY_MANIFEST")
while IFS=$'\t' read -r adapter_mac adapter_network; do
  network_info=$(v net-info "$adapter_network")
  grep -Eq '^Active: +yes$' <<< "$network_info" || fail 'The network is inactive.'
  network_xml=$(v net-dumpxml "$adapter_network")
  forward_mode=$(xmllint --xpath 'string(/network/forward/@mode)' - <<< "$network_xml")
  if [[ "$adapter_mac" == "$mac" ]]; then
    [[ "$forward_mode" == nat ]] || fail 'The test network must use NAT.'
  else
    [[ "$forward_mode" == nat || -z "$forward_mode" ]] || fail 'Additional libvirt networks must use NAT or isolated forwarding.'
  fi
  while IFS=$'\t' read -r candidate_mac _; do
    if grep -Fiq "$candidate_mac" <<< "$network_xml"; then
      fail 'The MAC address already has a declaration in this network.'
    fi
  done <<< "$adapters"
done <<< "$adapters"
v domcapabilities --virttype kvm --arch x86_64 --machine "$machine" >/dev/null

# MAC checks include stopped VMs and static DHCP reservations.
while IFS= read -r domain; do
  [[ -n "$domain" ]] || continue
  domain_xml=$(v dumpxml "$domain")
  while IFS=$'\t' read -r adapter_mac _; do
    if grep -Fiq "$adapter_mac" <<< "$domain_xml"; then
      fail 'The MAC address is already used by %s.' "$domain"
    fi
  done <<< "$adapters"
done <<< "$names"

nb_info 'Mode: %s; URI: %s; VM: %s' "$mode" "$uri" "$name"
nb_info 'Disk: %s (%s GiB); network: %s' "$disk" "$size" "$network"
while IFS=$'\t' read -r adapter_mac adapter_network; do
  nb_info 'Adapter: %s; network: %s' "$adapter_mac" "$adapter_network"
done <<< "$adapters"
nb_info 'Method: %s; ISO: %s; disk image: %s' "$method" "$iso" "$image"
if [[ "$mode" == dry-run ]]; then
  nb_success 'Checks passed. No disk or VM was created.'
  exit 0
fi

# Never clean up automatically on failure; inspect created resources first.
v vol-create-as "$pool" "$volume" "${size}G" --format qcow2
created_path=$(v vol-path "$volume" --pool "$pool")
[[ "$created_path" == "$disk" ]] || fail 'Libvirt created the disk at a different path: %s' "$created_path"
if [[ "$method" == image ]]; then
  v vol-upload "$volume" "$image" --pool "$pool"
fi
v define "$NIXBERRY_DOMAIN_XML"
if [[ "$method" == image ]]; then
  nb_success 'VM created with a prebuilt disk; install will start and verify it.'
  exit 0
fi
v start "$name"
nb_info 'Installation ISO started. The target NixOS is not installed yet.'
nb_info 'Console: virsh --connect %s console %s' "$uri" "$name"
nb_info 'Address: virsh --connect %s domifaddr %s --source lease' "$uri" "$name"
