# NixBerry

NixBerry sestavuje a spravuje NixOS servery na platformách **QEMU/KVM (libvirt),
Hyper-V, Proxmox VE, VirtualBox, VMware Workstation a Raspberry Pi 5**.
Jeden recept určuje systém;
deklarace každého stroje vybírá konfiguraci, platformu, síť a prostředky.

Výchozí nasazení VM používá hotový disk (`image`). Na všech VM platformách
lze zvolit instalaci z ISO. Raspberry Pi se obnovuje ze SD.

## Začít

Projekt je v aktivním vývoji. Podporované postupy a rozsah ověření popisují
[návod pro platformy](docs/vm.md) a [testovací plán](docs/test-plan.md).

Správcovský počítač potřebuje Linux x86_64, Nix s povolenými flakes a SSH.
Po naklonování vstup do kořene projektu. Zvolený adresář přidej do `PATH`,
aby další ukázky s příkazem `nixberry` fungovaly z libovolného adresáře:

```sh
export PATH="$PWD:$PATH"
nixberry --help
```

Před nasazením připrav vlastní veřejný SSH klíč a vybrané připojení podle
[šablon hostitelů](management/examples/hosts.nix):

```sh
cp ~/.ssh/id_ed25519.pub admin.pub
cp management/examples/hosts.nix hosts.local.nix
```

V `hosts.local.nix` ponech pouze své hostitele a uprav jejich parametry.
Pro první místní libvirt VM použij položku `local-libvirt`.
NixBerry očekává již připravený hypervizor, síť a úložiště.
Výhradně offline příprava hostitele nepotřebuje; klíč správce ano.
Po čistém klonování je seznam cílů prázdný.

```sh
nixberry list
nixberry --help
```

**Cíl** je deklarovaný server, **hostitel** je počítač s hypervizorem.
Jména existujících cílů ukazuje `list`; hostitelé jsou v [hosts.nix](hosts.nix).
Všechny názvy `vm-demo-*` v návodech jsou příklady — nahraď je svým cílem.

| Potřebuji | Návod |
| --- | --- |
| Zjistit stav, přihlásit se, aktualizovat | [Běžná správa](#sprava) |
| Vytvořit VM z disku nebo ISO | [Nová VM](#reprodukce) |
| Předat systém bez přístupu k hypervizoru | [Offline předání](#offline) |
| Upravit aplikace, služby nebo deklaraci | [Konfigurace](#konfigurace) |
| Obnovit Raspberry Pi | [Obnova ze SD](docs/rpi-recovery.md) |
| Najít příkaz nebo přepínač | [Reference CLI](docs/cli.md) |
| Řešit chybu nebo nedostupnost | [Při potížích](#potize) |

<a id="sprava"></a>

## Běžná správa

```sh
nixberry show vm-demo-libvirt
nixberry status vm-demo-libvirt
nixberry ssh vm-demo-libvirt
nixberry doctor vm-demo-libvirt
nixberry verify vm-demo-libvirt
```

`show` čte deklaraci a vypisuje dostupné operace. `status` zjišťuje živý stav;
bez cíle se ptá všech deklarovaných strojů souběžně. Pro rychlou kontrolu
jednoho serveru uveď jeho jméno. `doctor` hledá provozní problémy,
`verify` navíc kontroluje shodu s receptem.

<a id="aktualizace"></a>
<a id="porovnani-zmen"></a>

### Aktualizovat systém

Uprav recept nebo deklaraci a pokračuj:

```sh
nixberry diff vm-demo-libvirt
nixberry update vm-demo-libvirt
nixberry update vm-demo-libvirt --apply
nixberry verify vm-demo-libvirt
nixberry diffg vm-demo-libvirt
```

- `diff` porovnává běžící systém s aktuálním receptem; může sestavovat místně.
- `update` bez `--apply` ukáže plán bez připojení a sestavení.
- `update --apply` sestaví, přenese a aktivuje systém. Nemění kapacitu VM ani oddíly.
- `diffg` porovnává dvě nejvyšší dochované generace na serveru. Po aktualizaci
  bývá `diff` prázdný; změnu mezi generacemi ukáže `diffg`.

Postup je stejný pro VM i RPi. Aktualizace může restartovat služby, zařízení
automaticky nerestartuje. Závislosti mění samostatný
`nix flake update --flake .` z kořene tohoto receptu; aktualizace serveru používá
jeho aktuální lock. Podrobnosti o [generacích a snímku receptu](docs/reference.md#recept).

<a id="reprodukce"></a>

## Nová VM

V místním `hosts.local.nix`, načítaném přes [hosts.nix](hosts.nix), musí
existovat připravený hostitel se sítí a úložištěm.
NixBerry tyto prostředky nezakládá.

```sh
nixberry new-libvirt-vm vm-demo-libvirt --host local-libvirt
nixberry show vm-demo-libvirt
```

Vznikne místní `instances/vm-demo-libvirt.nix`. Zkontroluj v ní konfiguraci,
síť a kapacitu. Výchozí prostředky jsou 4 CPU, 4 GiB RAM a 32 GiB disk.
UUID a MAC zachovej pro další správu téže VM.

```sh
nixberry plan vm-demo-libvirt
nixberry reproduce vm-demo-libvirt --apply
nixberry verify vm-demo-libvirt
```

| Platforma | Příkaz pro deklaraci |
| --- | --- |
| QEMU/KVM (libvirt) | `new-libvirt-vm` |
| Hyper-V | `new-hyperv-vm` |
| Proxmox VE | `new-proxmox-vm` |
| VirtualBox | `new-virtualbox-vm` |
| VMware Workstation | `new-vmware-vm` |

Bez `--host` se vybere jediný deklarovaný hostitel dané platformy.
Při více možnostech jej vyber výslovně. `new-* --dry-run` jen vypíše návrh.
Proxmox VMID z místní evidence ještě nemusí být volné v clusteru; to ověří `plan`.

<a id="metody"></a>

### Disk nebo ISO

| Metoda | `create CÍL --apply` | `install CÍL --apply` |
| --- | --- | --- |
| `image` — výchozí | Vytvoří vypnutou VM s hotovým diskem. | Spustí ji a ověří systém z disku. |
| `iso` | Vytvoří VM s prázdným diskem a spustí ISO. | Nainstaluje systém, korektně vypne VM, odpojí ISO a ověří start z disku. |

`reproduce --apply` a `provision --apply` spojují oba kroky.
Metodu ukládá `deploymentMethod` v deklaraci; `--method image|iso` ji jednorázově
přepíše. Například:

```sh
nixberry new-proxmox-vm vm-demo-proxmox --host HOSTITEL --method iso
nixberry plan vm-demo-proxmox
nixberry reproduce vm-demo-proxmox --apply
```

Při odděleném `create` a `install` zvol stejnou metodu. Nasazení odmítá
existující cíl; ISO instalátor také odmítá disk s oddíly nebo signaturami.
Při chybě nejprve zjisti stav přes `status` a `probe`, potom rozhodni o pokračování.
[Podrobnosti nasazení a resetu](docs/vm.md).

<a id="offline"></a>

## Offline předání

Když hypervizor spravuje kolega, připrav deklaraci bez hostitele:

```sh
nixberry new-proxmox-vm vm-demo-proxmox-offline --offline
```

V deklaraci nastav kapacitu a síť. Výchozí je DHCP; pro přidělenou pevnou IP:

```nix
network = {
  mode = "static";
  address = "10.0.1.19/24";
  gateway = "10.0.1.1";
  dns = [ "10.0.1.1" ];
};
```

Adresy jsou příklad. Bridge, VLAN, firewall a připojení do správné podsítě
zajišťuje provozovatel hypervizoru. Potom připrav disk nebo ISO:

```sh
nixberry export vm-demo-proxmox-offline --output ~/vm-demo-proxmox-disk
nixberry export vm-demo-proxmox-offline --method iso --output ~/vm-demo-proxmox-iso
```

Výstupní adresář musí být nový a mimo recept. Balíček obsahuje médium,
manifest, recept s místními deklaracemi, kontrolní součty a návod pro provozovatele.
Sestavení může potřebovat internet; hotové médium obsahuje cílový systém.
**Export nezálohuje provozní data ani tajemství serveru.**

Po importu lze server spravovat přes `access` v deklaraci nebo jednorázově:

```sh
nixberry doctor vm-demo-proxmox-offline --ssh root@10.0.1.19:22
nixberry update vm-demo-proxmox-offline --ssh root@10.0.1.19:22 --apply
```

SSH přístup nepřidává správu hypervizoru. [Předání disku, ISO a Proxmox VM](docs/offline.md).

<a id="obnova-rpi"></a>

## Raspberry Pi 5

RPi má deklaraci v `management/rpi/targets/`; běžná správa používá stejné
`status`, `ssh`, `diff`, `update` a `verify`. Plnou obnovu zajišťuje SD:

```sh
nixberry export nixberry --output ~/nixberry-sd
```

Dále postupuj podle [obnovy RPi ze SD](docs/rpi-recovery.md).
Samotný start SD nic nemaže. Zápis na SSD vyžaduje výslovné provedení a kontrolu
identity RPi i disku. `--method` a reset hypervizoru se na RPi nevztahují.

<a id="konfigurace"></a>

## Konfigurace a místní deklarace

| Co nastavuji | Kde |
| --- | --- |
| Připojení a prostředky hypervizorů | [hosts.nix](hosts.nix) |
| Konkrétní VM, její síť a kapacitu | `instances/JMÉNO.nix` |
| RPi, jeho SSD a síť | [management/rpi/targets/](management/rpi/targets/default.nix) |
| Skladbu balíčků a služeb | [configurations/](configurations/default.nix) |
| Společný základ systému | [modules/common.nix](modules/common.nix) a `modules/` |
| Boot, disky, ovladače a guest podporu | `platforms/` |
| Veřejný klíč správce | Místní `admin.pub` |

Každý cíl volí jednu `configuration`; výchozí je `default`.
Volba `--configuration` vybírá konfiguraci při vytváření deklarace:

```sh
nixberry new-libvirt-vm vm-demo-nixoncpp --configuration nixoncpp
```

U existujícího stroje uprav pole v deklaraci a pokračuj přes `diff` a `update`.
Podrobný návod na [vlastní konfigurace, aplikace a sestavení](docs/reference.md).

**Konkrétní VM i RPi deklarace, `hosts.local.nix` a `admin.pub` se v Gitu neverzují.**
Místně fungují i bez `git add`; zálohuj je samostatně. V adresářích deklarací
se verzují jen `default.nix` a `.gitignore`. Po klonování vytvoř nebo obnov
svá místní data. Recept používá vlastní `flake.lock`.

<a id="potize"></a>

## Při potížích

| Situace | Další krok |
| --- | --- |
| Přehled trvá dlouho | Použij `status CÍL` místo všech hostitelů. |
| VM běží, ale server neodpovídá | `doctor CÍL`, `probe CÍL` a konzole hypervizoru. |
| Vytvoření hlásí existující VM | Zjisti stav; podle metody můžeš pokračovat přes `install`. |
| Po aktualizaci je `diff` prázdný | Použij `diffg CÍL`. |
| Přenos SSH přerušil aktualizaci | Zkontroluj `journalctl -u nixberry-update` na serveru a spusť `verify`. |
| Chci posoudit místo na disku | `cleanup CÍL` vypisuje náhled; nic nemaže. |
| Chci odstranit spravovanou VM | [Chráněný reset](docs/vm.md#reset), nejprve plán a `allowReset = true;`. |
| Hypervizor ukazuje téměř plnou RAM | Porovnej cache, `MemAvailable` a tlak na paměť; [postup](docs/reference.md#pamet). |

Zaseknutou interaktivní SSH relaci odpoj sekvencí **Enter, `~`, `.`**.
Při změně SSH host klíče nejprve nezávisle ověř otisk; starý záznam se automaticky nemaže.
[Podrobnosti SSH a guest agentů](docs/vm.md#prime-ssh).

## Shell a další návody

Bash doplňuje příkazy, cíle, hostitele, konfigurace a volby. Pro načtení v aktuálním shellu:

```sh
source ./completion.bash
```

Spouštěč a doplňování respektují `NIXBERRY_RECIPE`; jinak používají adresář
projektu. Proměnnou nastav pomocí `export`; neplatná výslovná cesta skončí
chybou. Projekt funguje samostatně a nemá závislost na Nixonu.

| Návod | Obsah |
| --- | --- |
| [CLI](docs/cli.md) | Všechny příkazy, přepínače a režimy zápisu. |
| [VM platformy](docs/vm.md) | Hostitelé, přímé SSH, guest agenti a reset. |
| [Offline předání](docs/offline.md) | Import disku, instalace z ISO a předání Proxmox VM. |
| [Obnova RPi](docs/rpi-recovery.md) | Příprava SD a obnova konkrétního SSD. |
| [Technická reference](docs/reference.md) | Moduly, konfigurace, aplikace a Nix výstupy. |
| [Testování](docs/test-plan.md) | Izolované kontroly a živá testovací matice. |
| [Jednotnost platforem](docs/platform-consistency.md) | Společný kontrakt a pravidla jeho kontroly. |
| [Uživatelské hlášky](docs/messages.md) | Proudy, barvy a překlady. |
| [Proxmox lab](docs/proxmox-lab.md) | Vnořený hostitel pro integrační zkoušky. |

## Licence

Autor: Tomas Mark. Zdrojový kód NixBerry je dostupný pod [licencí MIT](LICENSE).
Používané balíčky a nástroje mají vlastní licence uvedené u jejich projektů.
