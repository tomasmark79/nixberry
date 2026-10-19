# Offline balíček: disk, ISO nebo obnovovací SD

[← Hlavní návod](../README.md) · [Platformy a hostitelé](vm.md)

Balíček dodává systém z deklarace. Neobsahuje provozní data a tajemství
živého serveru. Příprava může stahovat připnuté závislosti; cílový systém
je potom součástí média. Aplikace mohou mít vlastní požadavky na síť.
Názvy `vm-demo-*` jsou ukázkové; použij vlastní deklaraci.

<a id="export"></a>

## Připravit balíček

Pro existující deklaraci vyber jedno médium a nový výstupní adresář:

```sh
nixberry export vm-demo-libvirt --output ~/vm-demo-libvirt-disk
nixberry export vm-demo-libvirt --method iso --output ~/vm-demo-libvirt-iso
nixberry export nixberry --output ~/nixberry-sd
```

U VM je výchozí `image`, případně `deploymentMethod` z deklarace.
`--method` ji přepíše pro tento export. Pro RPi se exportuje plná obnovovací SD.
Náhled `export … --dry-run` nic nesestavuje ani nezapisuje.
Výstup musí ležet mimo kořen receptu; relativní cesta se počítá od pracovního adresáře.

Pro kolegův hypervizor nejprve vytvoř novou deklaraci přes
`new-*-vm JMÉNO --offline`, nastav kapacitu a síť a exportuj její jméno.
Offline deklarace nepotřebuje přístup k hostiteli. Stejnou identitu zachovej
při dalších exportech; souběžná kopie potřebuje vlastní deklaraci a MAC.

| Soubor | Účel |
| --- | --- |
| `README.txt` | Postup pro vybrané médium. |
| `manifest.json` | Platforma, metoda, kapacita, síť a přesná cesta cílového systému. |
| `SHA256SUMS` | Kontrolní součty všech souborů. |
| `recipe/` | Stejný recept a lock, ze kterého vzniklo médium. |
| `INSTALL-ISO.sh` | Kontrola a instalace uvnitř VM; pouze v ISO balíčku. |
| `PROXMOX.txt`, `VIRTUALBOX.txt` | Předání hotového disku příslušné platformy. |

Před přenosem i po něm spusť **v kořeni balíčku**:

```sh
sha256sum -c SHA256SUMS
```

Ve Windows lze hodnotu jednotlivého média porovnat přes
`Get-FileHash -Algorithm SHA256 CESTA` s odpovídajícím řádkem `SHA256SUMS`.
Dřívější export je samostatný snímek. Jeho obsah se nemění s pracovní kopií;
pro jinou konfiguraci připrav nový balíček.

<a id="disk"></a>

## Spustit hotový systémový disk

Importuj **vlastní kopii** obrazu do nové VM. Instalační ISO při metodě `image`
nepřipojuj. Nastav CPU a RAM podle manifestu, UEFI a vypnutý Secure Boot.

| Platforma | Obraz a řadič | Guest služba |
| --- | --- | --- |
| libvirt/QEMU | QCOW2, VirtIO Block `/dev/vda` | `qemu-guest-agent`, připojený komunikační kanál |
| Hyper-V | VHDX, Generation 2, SCSI `/dev/sda` | `hv-kvp`, `hv-vss` |
| Proxmox | QCOW2, `virtio0` nebo VirtIO SCSI `scsi0` | QEMU Guest Agent povolený v konfiguraci VM |
| VirtualBox | VDI, SATA | Guest Additions, služba `virtualbox` |
| VMware Workstation | VMDK, SATA | VMware Tools, služba `vmware` |

Pro další kopii VDI použij `VBoxManage clonemedium disk`, aby měla vlastní UUID média.
Při vytváření VirtualBox VM přeskoč bezobslužnou instalaci a připoj existující disk.
Hyper-V přiděluje vlastní VM ID a BIOSGUID.

Síť připoj do prostředí odpovídajícího `network` v manifestu. Při DHCP musí
být dostupný DHCP server; pevná IP vyžaduje správnou podsíť a bránu.
Ruční import VirtualBoxu může použít Bridged Adapter nebo NAT; u NAT nastav
forward SSH portu. Automatický adaptér používá bridged síť.

Po startu ověř z konzole VM:

```sh
hostname
ip -4 address
ip route
resolvectl status
findmnt /
findmnt /boot
readlink -f /run/current-system
systemctl --failed
systemctl is-active sshd systemd-networkd
```

Cesta `/run/current-system` musí odpovídat `systemPath` v manifestu.
Zkontroluj také guest službu platformy. Nově vzniklé SSH host klíče ověř
před přihlášením. Veřejné HTTPS ani NTP nejsou podmínkou samotného offline startu.

Ruční import nevytváří značky vlastnictví automatické správy hypervizoru.
Shodná MAC a UUID samy nestačí k převzetí VM adaptérem. Pro správu serveru
použij [přímé SSH](vm.md#prime-ssh).

<a id="iso"></a>

## Nainstalovat systém z ISO

Použij ISO balíček **konkrétní deklarace a platformy**. Profily mají různé
disky a ovladače; média různých platforem nejsou zaměnitelná.

1. Připrav novou VM s UEFI, vypnutým Secure Boot a jedním prázdným diskem
   o velikosti z manifestu. Použij řadič a guest službu z tabulky výše.
   Pro instalační Proxmox VM použij VirtIO Block `/dev/vda`.
2. Připoj ISO a spusť instalační prostředí `nixberry-installer`.
   Na Proxmoxu nejprve nahraj médium do úložiště s povoleným obsahem `iso`
   a připoj jeho spravovaný svazek; běžná cesta mimo úložiště nestačí.
   Přenes do něj `INSTALL-ISO.sh` přes SSH nebo další médium.
3. V konzoli hypervizoru ověř, že jde o správnou VM, a zjisti její DMI UUID
   pomocí `cat /sys/class/dmi/id/product_uuid`.
4. Podle `README.txt` spusť jako root příkaz s `dry-run`, ověřeným UUID,
   systémem, disko, kapacitou, profilem a cílovým diskem. Až kontrola projde,
   zopakuj stejný příkaz s `apply` místo `dry-run`.
5. Po úspěšné instalaci VM korektně vypni, odpoj ISO a spusť systém z disku.
   Ověř síť, služby a přesný `systemPath` stejně jako při importu disku.

ISO obsahuje připravený systém i disko; instalace nepoužívá síťové substitutery.
Skript odmítá nesprávnou identitu, profil či kapacitu a disk s oddíly nebo
signaturami. Pokud instalace selže, ponech ISO připojené a nejprve zjisti stav;
opakovaný zápis na částečně nainstalovaný disk není povolený.

Spravovaná VM absolvuje stejné kroky automaticky přes
`nixberry reproduce CÍL --method iso --apply`; používá přitom i kontroly hostitele.

<a id="proxmox-vm103"></a>

## Ukázka předání pro Proxmox VM103: vm-demo-proxmox

Tento scénář nepotřebuje přístup k Proxmoxu ani generování jeho `.conf`.
Provozovatel vytvoří VM, my dodáme systémový disk. Nejprve vytvoř ukázkovou deklaraci:

```sh
nixberry new-proxmox-vm vm-demo-proxmox --offline
```

V `instances/vm-demo-proxmox.nix` nastav `spec.vmid = 103` a síť podle tabulky
níže. VMID i adresy nahraď hodnotami přidělenými pro svou VM.
Potom zkontroluj deklaraci a připrav export:

```sh
nixberry show vm-demo-proxmox
nixberry export vm-demo-proxmox --method image --output ~/vm-demo-proxmox-vm103
```

| Parametr | Ukázková hodnota |
| --- | --- |
| VMID | `103` |
| CPU / RAM / disk | 4 CPU / 4 GiB / 32 GiB |
| Hostname uvnitř serveru | `nixberry-vm-demo-proxmox` |
| IP | `10.0.1.19/24` |
| Brána a DNS | `10.0.1.1` |

Provozovatel nastaví UEFI/OVMF, vypnutý Secure Boot, boot ze systémového
disku a jednu VirtIO síťovou kartu do uvedené podsítě. Povolí QEMU Guest Agent
a importuje kopii QCOW2 jako `virtio0` nebo VirtIO SCSI `scsi0`.
Řiď se také souborem `PROXMOX.txt` v exportu; VMID a prostředky musí být na cíli volné.

Oddíly se připojují přes `disk-system-root` a `disk-system-boot`, takže obraz
startuje i po změně `/dev/vda` na `/dev/sda`. Při potížích zkontroluj
`lsblk -o NAME,TYPE,PARTLABEL,MOUNTPOINTS`; neměň řadič jen kvůli názvu zařízení.
Větší virtuální disk se tímto postupem automaticky nerozdělí na větší oddíly.

Provozovatel může přidělit vlastní název VM, MAC a UUID. Hostname systému
zůstává podle deklarace a síť není vázaná na původní MAC. Po startu ověří
adresu, bránu, DNS a `systemPath`. Dostupnost v kolegově prostředí ověřuje provozovatel.

Pro následnou správu z Nixonu nastav samostatný SSH endpoint v `access`.
Tento údaj nemění IP uvnitř obrazu. Proxmox bridge, VLAN ani firewall obraz nenastavuje.

<a id="rpi"></a>

## Obnovovací SD pro RPi

Plný `img.zst` připravuje prostředí i cílový systém pro konkrétní RPi a SSD.
Pokračuj podle [obnovy ze SD](rpi-recovery.md). Menší síťová SD vyžaduje
přenos cílového store; pro místní obnovu bez internetu použij plný obraz.

Offline spuštění média neznamená, že lze bez připravených závislostí také
spustit nové sestavení na správci. Samotný zdrojový recept a lock nejsou
kopií všech závislostí Nix store. Pro místní instalátor jsou potřebné součásti na plné SD.

## Přímé sestavení média

Pro běžné předání použij `export CÍL`: zvolí správný systém, médium a přibalí návod.
Technické výstupy `nixberryImages.CÍL` a `nixberryInstallers.CÍL` jsou popsané
v [referenci](reference.md#media). Generické obrazy mají výchozí profil a hostname;
nenahrazují export konkrétní instance.
