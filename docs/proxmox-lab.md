# Vnořený Proxmox na Nixonu

[← Hlavní návod](../README.md) · [VM platformy](vm.md) · [Testování](test-plan.md)

Samostatný lab slouží pro zkoušení Proxmox adaptéru nixberry. Je to hostitel
Proxmox VE, nikoli další NixOS profil společného serveru.

- Libvirt VM `nixberry-pvelab`, 6 vCPU, 12 GiB RAM, CPU `host-passthrough`, UEFI.
- Disky `/home/shared/vms/nixberry-pvelab/system.qcow2` (32 GiB)
  a `data.qcow2` (64 GiB), s odlišnými serialy.
- Vlastní libvirt NAT síť `nixberry-pvelab`, bridge `virbrpve0`, podsíť
  `192.168.78.0/24`. DHCP rezervuje `192.168.78.10` pro jedinou kartu labu.
- Uvnitř Proxmoxu je `vmbr0`, staging `/var/lib/nixberry` a LVM thin úložiště
  `lab-vm` na samostatném virtuálním datovém disku.
- VM ani její síť nemají autostart. Ostatní VM a existující síť `default`
  se při přípravě nemění.

## Přístup a běžné ověření

Web: **https://192.168.78.10:8006**, účet `root`, realm **Linux PAM**.
Heslo je pouze v `~/.local/state/nixberry/proxmox-lab/root-password` s režimem
`0600`; instalační odpověď a SSH known hosts jsou ve stejném soukromém adresáři.
Pro web se používá certifikát vlastní CA tohoto Proxmoxu.

```sh
ssh root@192.168.78.10
virsh -c qemu:///system domstate nixberry-pvelab
virsh -c qemu:///system net-start nixberry-pvelab # pouze pokud je síť neaktivní
virsh -c qemu:///system start nixberry-pvelab   # pouze pokud je vypnutý
```

Před používáním ověř SSH, web/API, identitu VM, dostupné KVM a start
vnořeného QEMU. Samotný běžící proces libvirtu nepotvrzuje zdraví Proxmoxu.
Po instalaci zkontroluj odpojení automatického média i v trvalé konfiguraci VM.

Do `hosts.local.nix` přidej `pvelab-proxmox` podle šablony Proxmoxu:
`sshHost = "192.168.78.10"`, `hostName = "pvelab"`, `node = "pvelab"`,
`storage = "lab-vm"`, `isoStorage = "local"` a `bridge = "vmbr0"`.
Další Nixberry VM připrav:

```sh
nixberry new-proxmox-vm vm-demo-proxmox --host pvelab-proxmox
nixberry plan vm-demo-proxmox
# Po kontrole plánu:
nixberry reproduce vm-demo-proxmox --apply
```

Tento postup vytváří novou VM uvnitř Proxmoxu. Před nasazením zkontroluj
volné VMID, kapacitu, úložiště `lab-vm` a bridge `vmbr0` v nové deklaraci.
Výchozí metoda je `image`; pro instalaci z ISO přidej `--method iso`.

```sh
nixberry status vm-demo-proxmox
nixberry ssh vm-demo-proxmox
nixberry doctor vm-demo-proxmox
nixberry verify vm-demo-proxmox
nixberry cleanup vm-demo-proxmox --json
```

`cleanup` chrání deklarovanou VM a hlásí její kapacitu; fyzická alokace thin
úložiště může zůstat neznámá. Opakovaný `plan` existující cíl odmítne.
Adresa při DHCP je dynamická; aktuální hodnotu
čti přes `status`, nevkládej ji do deklarace jako trvalý údaj.

## Opakovatelná příprava nového labu

Z kořene receptu nixberry:

```sh
pvelab_tools=$(nix-build management/proxmox/lab/tools.nix --no-out-link)
export PATH="$pvelab_tools/bin:$PATH"
python3 management/proxmox/lab/create.py --dry-run
python3 management/proxmox/lab/create.py --apply
```

`--apply` stáhne oficiální Proxmox VE 9.2-1 ISO a ověří připnutý SHA-256.
Oficiální `proxmox-auto-install-assistant` připraví automatické médium.
Instalátor používá výhradně nový `vda`; datový disk `vdb` nechává prázdný.
Konzolová klávesnice je `en-us`, země Česko a časové pásmo `Europe/Prague`.
Root dostane náhodné heslo a veřejný SSH klíč z `admin.pub`.
Soukromé údaje nevstupují do Nix store ani Gitu.

Po dokončení instalace a startu z disku:

```sh
python3 management/proxmox/lab/finish.py
python3 management/proxmox/lab/finish.py --apply
```

Dokončení ověřuje UUID, hostname a serialy obou disků, potom nastaví LVM thin
pool na prázdném datovém disku. Rozpoznaný obsah nepřepisuje.
První SSH klíč připne do soukromého `known_hosts` labu. Před použitím běžných
příkazů nixberry ověř stejný klíč také pro svůj běžný SSH přístup.

### Aktualizace labu

Před zátěžovou zkouškou zkontroluj `uname -r`, stav KVM a dostupné aktualizace
uvnitř labu. Repozitář musí odpovídat jeho verzi Proxmoxu a předplatnému.

```sh
uname -r
apt list --upgradable
```

Aktualizaci hostitele prováděj samostatně podle jeho správy Proxmoxu,
s korektně vypnutými vlastními testovacími VM. Po restartu zkontroluj
skutečné `uname -r`, KVM, úložiště a nový test obou metod.

`create.py` odmítá existující VM, UUID, síť, disky i soukromý stav.
Neslouží jako reset ani automaticky nemaže částečný výsledek. Při přerušení
nejprve prohlédni konkrétní stav; žádný stávající disk není pokynem k přepsání.
Pokud původní VM i disky už byly odstraněné, uchovej stará metadata a soukromý
stav mimo výchozí adresáře. Zbylou síť odstraň jen po ověření jejího UUID,
podsítě a nepřítomnosti odkazů z ostatních VM. Oficiální ISO lze znovu použít;
skript vždy ověří jeho SHA-256. Nový SSH klíč přijmi až po ověření nové VM
přes libvirt a soukromý `known_hosts` labu; starý záznam před nahrazením zálohuj.
Validace zachytává i chybu `Error:` na stderr asistenta při návratovém kódu 0.

### Záznam kernel panicu

Sériový port Proxmox VM zapisuje na Nixonu do
`/var/log/libvirt/qemu/nixberry-pvelab-serial.log`. Libvirt při dalším startu
připojuje výstup na konec stejného souboru. Záznam je mimo guest systém,
takže jej lze číst i při nedostupném SSH nebo kernel panicu Proxmoxu:

```sh
sudo tail -F /var/log/libvirt/qemu/nixberry-pvelab-serial.log
```

`create.py` nastavuje záznam portu a `finish.py --apply` doplní v Proxmoxu
`/etc/default/grub.d/99-nixberry-serial.cfg` a spustí `update-grub`.
Kernel používá současně obrazovku a `ttyS0` rychlostí 115200, s `loglevel=7`;
GRUB má rovněž zapnutý sériový terminál. Parametry kernelu platí od dalšího
startu. Na běžícím Proxmoxu je ověř přes `cat /proc/cmdline`, na Nixonu
pak zkontroluj přibývající kernelové zprávy v sériovém logu.

Samotný stav `running` v libvirtu není důkazem zdravého Proxmoxu: při kernel
panicu může proces QEMU dál běžet. Před restartem zachovej sériový log;
uložený journal nemusí obsahovat poslední chybu ani její stack trace.

Zdroje: [oficiální ISO](https://enterprise.proxmox.com/iso/),
[instalační asistent Proxmoxu](https://github.com/proxmox/pve-installer).
