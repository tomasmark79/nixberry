# VM platformy, hostitelé a SSH

[← Hlavní návod](../README.md) · [CLI](cli.md) · [Offline předání](offline.md)

<a id="hostitele"></a>
<a id="podpora"></a>

## Připravit hostitele

Připojení a existující prostředky zapiš do místního `hosts.local.nix`;
[hosts.nix](../hosts.nix) jej načítá a bez něj vrací prázdnou evidenci.
Šablony pro všech pět hypervizorů jsou v
[management/examples/hosts.nix](../management/examples/hosts.nix).
Přidání záznamu samo neinstaluje hypervizor, nevytváří síť ani úložiště.

| Backend | Hostitelské prostředí | Systémový disk a síť |
| --- | --- | --- |
| `libvirt` | Místní Linux, KVM, `qemu:///system` | QCOW2, VirtIO Block, deklarovaná NAT síť s DHCP. |
| `hyperv` | Windows s Hyper-V a SSH | VHDX, Generation 2, SCSI, deklarovaný switch. |
| `proxmox` | Proxmox VE, SSH na deklarovaný uzel | VirtIO disk, existující bridge, QEMU Guest Agent. |
| `virtualbox` | Linux místně nebo Linux/Windows přes SSH | VDI, SATA, existující bridged adaptér, Guest Additions. |
| `vmware` | Workstation na Linuxu místně nebo Linux/Windows přes SSH | VMDK monolithicSparse, SATA, NAT s DHCP, VMware Tools. |

Všechny VM používají UEFI a vypnutý Secure Boot. Adaptéry spravují nové vlastní
VM; nepřebírají existující cizí stroje. Libvirt podporuje místní URI,
VMware adaptér podporuje Workstation, nikoli ESXi, vCenter nebo Fusion.

Automatický přístup k hostitelům používá soukromé IPv4. Adresa
`192.168.79.1` je chráněná a nesmí být spravovaným cílem ani jump hostitelem.

### Libvirt

Připrav KVM, oprávnění k `qemu:///system`, aktivní storage pool, síť s DHCP,
dostatek místa a podporovaný machine type. Parametry jsou v připojení
hostitele; OVMF a médium dodává recept.

`image` importuje QCOW2 do nového svazku, `iso` vytváří prázdný disk
`/dev/vda`. Přístup do VM používá její adresu z DHCP deklarované sítě.

### Hyper-V

Účet SSH potřebuje oprávnění spravovat Hyper-V a zapisovat do vyhrazeného
úložiště. Připrav OpenSSH, ověřený host klíč a TCP forwarding pro jump host.
Připojení určuje `windowsComputerName`, `switchName`, `switchId` a úložiště.

VM má Generation 2, vypnutý Secure Boot a pevnou kapacitu RAM.
Hyper-V přiděluje skutečné VM ID i BIOSGUID; adaptér kontroluje jejich vazbu
na owner záznam. SSH do VM může vést přes Windows hostitele.
Služby v cílovém systému jsou `hv-kvp` a `hv-vss`.

### Proxmox

Hostitel potřebuje Python 3, `qm`, `pvesh`, příslušná oprávnění a existující
`storageDirectory`, `storage`, `bridge` a `node`. SSH musí směřovat
na deklarovaný uzel. Připrav QEMU Guest Agent a jeho komunikační kanál ve VM.

`image` používá import hotového disku. Pro `iso` je navíc nutné úložiště
s obsahem `iso`; určuje jej `isoStorage`, výchozí je `local`.
Instalační ISO se připojuje jako spravovaný svazek, nikoli libovolná cesta.
Po instalaci a vypnutí adaptér odpojí a odstraní vlastní kopii média.
Plán kontroluje místo pro staging, disk i EFI svazek; nepovoluje automatické
přealokování thin úložiště.

Pro integrační zkoušky lze připravit [vnořený Proxmox lab](proxmox-lab.md).

### VirtualBox

Hostitel potřebuje Python 3, funkční `VBoxManage`, registr VM stejného uživatele
a bridged adaptér uvedený v deklaraci. Ověř například:

```sh
VBoxManage --version
VBoxManage list bridgedifs
```

Na NixOS jsou potřeba hostitelský modul a oprávnění uživatele k VirtualBoxu.
Ve Windows používej pro SSH i GUI stejný účet. Adaptér přidává oprávnění SID
tohoto účtu jen do nového soukromého adresáře VM.

### VMware Workstation

Připrav Python 3, funkční `vmrun`, vyhrazené úložiště a NAT síť s DHCP.
Metoda `iso` potřebuje také `vmware-vdiskmanager` vedle `vmrun`.
Na Windows používej pro SSH a GUI stejný účet; na NixOS musí fungovat
hostitelské moduly VMware i NAT/DHCP.

`toolPath` může obsahovat úplnou cestu s mezerami. `vmrun` spravuje VMX
v deklarovaném úložišti; správa nezaručuje její zobrazení v seznamu GUI.
Vlastní VMX lze otevřít přímo ve Workstation.

### Společné požadavky přenosného adaptéru

Proxmox, VirtualBox a VMware používají Python pod `pythonCommand`.
Ve Windows ověř interpreter přímo v SSH relaci; alias Microsoft Store nestačí.
`hostName` musí odpovídat `socket.gethostname()`.

`storageDirectory` musí existovat a umožňovat zápis. Nástroj vytváří jen nový
podadresář VM; odmítá kořen disku, přesměrování odkazy a přepis cizího obsahu.
Nix běží na správci, nikoli na vzdáleném hostiteli. Vzdálené SSH vyžaduje
ověřený klíč a forwarding pro přístup do VM přes jump host.

<a id="prime-ssh"></a>

## Spravovat server bez hypervizoru

Offline VM může používat přímou správu systému. Do deklarace vedle `spec` přidej:

```nix
access = {
  host = "server.example";
  port = 22;
  user = "root";
  knownHostsFile = null;
  hostKeyAlias = null;
};
```

Fungují `status`, `ssh`, `doctor`, `verify`, `diff`, `diffg` a `update`.
Jednorázový endpoint vybírá `--ssh`:

```sh
nixberry doctor vm-demo-proxmox --ssh root@server.example:2030
```

`access` ani `--ssh` nemění IP v obrazu, systémovou konfiguraci nebo
vlastnictví VM. Podporovaný účet je `root`. Pro tyto příkazy lze místo
`--ssh` použít také `--host root@HOST:PORT`; u `new-*` má `--host`
význam hostitele hypervizoru.

Vlastní `knownHostsFile` musí být absolutní cesta; `null` používá běžný
SSH soubor. Ověřený klíč u vlastního portu ulož pod `[HOST]:PORT`.
`hostKeyAlias` dává stabilní jméno při změně endpointu. Změna adresy nepřijímá
nový klíč automaticky a jednorázové SSH zachovává deklarované ověřování klíče.

Offline cíl bez SSH přístupu má `show` a `export`; `status` a `doctor`
vysvětlí chybějící nastavení. Přímé SSH nepovoluje vytvoření ani reset VM.
Pro automatické vytvoření nové VM použij deklarovaný `host` místo
`backend` a odstraň `access`; existující ruční import se tím nepřevezme.

## SSH a guest agenti při potížích

Klíč správce odpovídá `admin.pub`. Automatické nasazení ukládá ověřený
SSH klíč cílového systému do `~/.local/state/nixberry/portable/UUID/known_hosts`
a respektuje `XDG_STATE_HOME`. Instalační prostředí používá dočasný soubor.
Hyper-V navíc používá stabilní alias při změně IP.

Při `HOST IDENTIFICATION HAS CHANGED` ověř otisk nezávisle. Starý klíč se
automaticky nemaže. Navázání SSH má limit 10 sekund; při ztrátě odezvy
navázaného serveru se spojení obvykle ukončí přibližně za 45 sekund.
Interaktivní relaci odpojí sekvence Enter, `~`, `.`.

QEMU Guest Agent potřebuje ve VM port `/dev/virtio-ports/org.qemu.guest_agent.0`.
Když chybí, provozovatel Proxmoxu nejprve ověří identitu stroje a zapne
**Options → QEMU Guest Agent**. Změna virtuálního zařízení může vyžadovat
úplné vypnutí a nový start VM. Uvnitř serveru ověř:

```sh
ls -l /dev/virtio-ports/
systemctl status qemu-guest-agent
```

Samotná instalace služby nevytvoří chybějící virtuální port.
Při přímém SSH do Proxmox VM je guest agent volitelný; neaktivní službu
`doctor` hlásí jako upozornění. Hostname, architektura a systémové oddíly se kontrolují dál.

<a id="reset"></a>

## Odstranit spravovanou VM

V její místní deklaraci nastav `allowReset = true;`, potom:

```sh
nixberry reset vm-demo-libvirt
nixberry reset vm-demo-libvirt --apply
```

První příkaz jen ověřuje plán. Druhý **odstraní VM a její ověřené vlastní
prostředky**. Deklarace zůstává na disku. Reset nepotřebuje sestavení systému
ani média a neplatí pro offline VM nebo RPi.

Kontroluje vlastníka, skutečnou identitu, disky, sdílení, snapshoty a odkazy.
Běžící VM vypne korektně s časovým limitem, před odstraněním vše znovu ověří.
Při nejistotě skončí chybou; nepřechází na tvrdé vypnutí a nemaže zbytky naslepo.

| Backend | Prostředky a hranice kontroly |
| --- | --- |
| libvirt | Vlastní QCOW2, NVRAM, vazby domén, podkladové obrazy a snapshoty. |
| Hyper-V | Owner, skutečné VM ID, disk a checkpointy; sdílené médium v `media/` ponechá. |
| Proxmox | VMID/uzel, systémový a EFI svazek, vlastní ISO, snapshoty a reference VM i kontejnerů. |
| VirtualBox | VM, disk a vlastní adresář; prověřuje registr médií a snapshoty. |
| VMware | Vlastní adresář, VMX a samostatný VMDK; kontroluje běžící VM a soubory pod vyhrazeným úložištěm. Vypnuté VMX mimo něj neprochází a seznam GUI neupravuje. |

Po přerušení nejprve prohlédni `status` a úložiště; operace se automaticky
neopakuje. `cleanup` zůstává pouze náhledem. Smazání souboru deklarace samo
VM ani disk neodstraní. [Testovací matice a bezpečnostní kontroly](test-plan.md).
