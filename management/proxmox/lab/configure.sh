#!/usr/bin/env bash
# Spouští se pouze přes finish.py s přiloženým společným helperem hlášek.
set -euo pipefail
fail() { nb_error "$@"; exit 1; }
[[ $# == 1 && $EUID == 0 ]] || fail 'Konfigurace labu vyžaduje root a očekávané UUID.'
[[ $(hostname -s) == pvelab && $(cat /sys/class/dmi/id/product_uuid) == "$1" ]] || fail 'Identita Proxmox labu neodpovídá deklaraci.'
[[ $(lsblk -dn -o SERIAL /dev/vda) == nixberry-pve-os ]] || fail 'Systémový disk nemá očekávaný serial.'
[[ $(lsblk -dn -o SERIAL /dev/vdb) == nixberry-pve-data ]] || fail 'Datový disk nemá očekávaný serial.'
pveversion >/dev/null
install -d -m 755 /etc/default/grub.d
cat > /etc/default/grub.d/99-nixberry-serial.cfg <<'EOF'
# Kernel i GRUB zapisují do sériového portu, který zaznamenává vnější libvirt.
GRUB_CMDLINE_LINUX="${GRUB_CMDLINE_LINUX:-} console=tty0 console=ttyS0,115200n8"
GRUB_CMDLINE_LINUX_DEFAULT="${GRUB_CMDLINE_LINUX_DEFAULT:-} loglevel=7"
GRUB_TERMINAL="console serial"
GRUB_SERIAL_COMMAND="serial --unit=0 --speed=115200 --word=8 --parity=no --stop=1"
EOF
nb_info 'Nastavuji sériovou konzoli pro záznam případného kernel panicu…'
update-grub
[[ -c /dev/kvm ]] || fail 'Proxmox nemá dostupné zařízení KVM.'
if ! vgs nixberry-lab >/dev/null 2>&1; then
    # Zapisujeme výhradně do nového prázdného virtuálního disku s ověřeným serialem.
    [[ $(lsblk -nr -o NAME /dev/vdb | wc -l) == 1 ]] || fail 'Datový disk má oddíly nebo další připojená zařízení.'
    [[ -z $(lsblk -nr -o MOUNTPOINTS /dev/vdb | tr -d '[:space:]') ]] || fail 'Datový disk je připojený.'
    if blkid -p /dev/vdb >/dev/null 2>&1; then
        fail 'Datový disk obsahuje rozpoznaná data; nic nemažu.'
    fi
    nb_info 'Připravuji prázdný datový disk labu jako LVM thin úložiště…'
    pvcreate /dev/vdb
    vgcreate nixberry-lab /dev/vdb
    lvcreate --type thin-pool -l 95%FREE -n data nixberry-lab
fi
[[ $(pvs --noheadings -o vg_name /dev/vdb | xargs) == nixberry-lab ]] || fail 'Datový disk nepatří do očekávané LVM skupiny.'
[[ $(vgs --noheadings -o pv_count nixberry-lab | xargs) == 1 ]] || fail 'LVM skupina používá další disk.'
lvs nixberry-lab/data >/dev/null
if ! pvesh get /storage/lab-vm --output-format json >/dev/null 2>&1; then
    pvesm add lvmthin lab-vm --vgname nixberry-lab --thinpool data --content images,rootdir
fi
pvesh get /storage/lab-vm --output-format json | python3 -c '
import json,sys
s=json.load(sys.stdin)
assert s["type"]=="lvmthin" and s["vgname"]=="nixberry-lab" and s["thinpool"]=="data", s
'
[[ -d /sys/class/net/vmbr0/bridge ]] || fail 'Proxmox nemá očekávaný bridge vmbr0.'
install -d -m 700 /var/lib/nixberry
nb_success 'Proxmox lab má připravené úložiště lab-vm, bridge vmbr0 a KVM.'
printf '{"storage":"lab-vm","bridge":"vmbr0","kvm":true}\n'
