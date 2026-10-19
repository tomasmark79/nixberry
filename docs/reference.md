# Technická reference

[← Hlavní návod](../README.md) · [CLI](cli.md) · [Testování](test-plan.md)

## Rozdělení konfigurace

| Oblast | Zdroj |
| --- | --- |
| Hostitelé a jejich připojení | Místní `hosts.local.nix`, načítaný přes [hosts.nix](../hosts.nix) |
| Místní VM deklarace a loader | [instances/](../instances/default.nix) |
| Konfigurace serverů | [configurations/](../configurations/default.nix) |
| Společný základ | [modules/common.nix](../modules/common.nix) |
| Služby, balíčky, nastavení systému | `modules/services/`, `modules/applications/`, `modules/system/` |
| Hardware a bootování | `platforms/rpi/`, `platforms/vm/` |
| Fyzické RPi a SSD | [management/rpi/targets/](../management/rpi/targets/default.nix) |
| Složení systémů, médií a nástrojů | [flake.nix](../flake.nix) |
| Registr a CLI | [management/deployment/](../management/deployment/default.nix) |
| Sdílené VM adaptéry | [portable.py](../management/deployment/portable.py), [host_agent.py](../management/deployment/host_agent.py) |

Služby pro konkrétní použití patří do vybrané konfigurace. Společné části
patří do `modules/`. Platforma řeší boot, disky, ovladače a guest podporu;
nevybírá aplikační konfiguraci. Identita, síť a prostředky patří do deklarace.

NixBerry má vlastní recept i lock a funguje bez osobní konfigurace notebooku.
V `instances/` a `management/rpi/targets/` se verzují jen loadery a pravidla
ignorování. Konkrétní VM i RPi deklarace, `hosts.local.nix` a `admin.pub`
zálohuj samostatně. Používej `path:` vstup, který je zahrnuje i bez `git add`.
Prázdná evidence je platná, neznámý hostitel je chyba.

## Pojmenované konfigurace

Každý cíl vybírá jednu `configuration`; výchozí je `default`.
Platí stejně pro VM, RPi, aktualizaci, image, ISO i export.

Novou konfiguraci vytvoř například jako `configurations/web.nix`:

```nix
{ ... }:
{
  imports = [ ../modules/common.nix ];
  services.nginx.enable = true;
}
```

Konfigurace importuje společný základ, nikoli jinou konfiguraci.
Službě podle potřeby otevři firewall. Platformu připojí flake automaticky.

```sh
nixberry new-libvirt-vm vm-demo-web --configuration web
nixberry show vm-demo-web
nixberry plan vm-demo-web
nixberry reproduce vm-demo-web --apply
```

`web` je příklad, který musíš nejprve vytvořit. U existujícího cíle uprav
`configuration` v deklaraci a pokračuj přes `diff` a `update`.
`--configuration` platí jen při vytváření deklarace.

Název začíná malým písmenem a obsahuje malá písmena, číslice a pomlčky.
Větší konfigurace může mít `configurations/web/default.nix`; souběžný
`web.nix` je chyba. Pomocné moduly ukládej dovnitř adresáře.
Katalog ignoruje symlinky a jeho výpis nesestavuje systémy:

```sh
nix eval --json path:.#nixberryConfigurations
```

Obsah modulů a podporu balíčků na ARM ověřuje až vyhodnocení a sestavení
příslušného systému. Změna společného základu ovlivňuje všechny konfigurace;
změna jednoho modulu jen jeho uživatele. Aktivace může restartovat služby
a měnit firewall. Rollback systému nevrací migrace databází ani provozní data.

### Konfigurace nixoncpp

`configuration = "nixoncpp";` přidává konzolovou aplikaci
[NixonCpp](https://github.com/tomasmark79/NixonCpp) a její knihovnu.
[Modul](../configurations/nixoncpp.nix) používá hotový balíček upstream flake
v podadresáři `nix`; zdroj připíná společný lock.

```sh
NixonCpp --help
NixonCpp
```

Aplikace se spouští ručně, nevytváří službu ani neotevírá porty.
`--write2file` ukládá `application.log` do pracovního adresáře.

## Identita a klíče

Název souboru určuje cíl CLI. Hostname VM je standardně `spec.name`,
přepsat jej lze přes `guestHostName`. Pro další kopii vytvoř novou deklaraci
s novým UUID a MAC; pro správu téže VM je zachovej.

`admin.pub` obsahuje veřejný klíč správce. Připrav jej před sestavením médií;
změnu na běžícím serveru aktivuje až `update`. Soukromý klíč do receptu nepatří.
Hostitele odeber až po odebrání nebo přesměrování jeho deklarací.

<a id="recept"></a>

## Snímek receptu a systémové generace

Nasazení, export a aktualizace používají neměnný snímek receptu.
Při změně deklarace oproti již spuštěnému CLI operaci spusť znovu;
nástroj nepřechází potichu na jiný cíl. Dočasné GC roots chrání zdroj
a sestavený výstup po dobu práce.

Export přibaluje stejný snímek jako `recipe/`; nemění se s pracovní kopií
a není kopií všech závislostí Nix store. Pro jiné nastavení vytvoř nový balíček.

`update --apply` ověří identitu a instalaci na disku, přenese systém
a aktivuje jej přes jednotku `nixberry-update` s vlastním prostředím.
Po aktivaci kontroluje systém a služby. Přerušené SSH proto není důkaz
ukončené aktivace. `diffg` porovnává nejvyšší dochované generace,
nikoli nutně právě aktivní a předchozí nabootovanou generaci.

<a id="diagnostika"></a>

## Diagnostika a místo na disku

`doctor` kontroluje identitu, síť, služby, bloky a inody na `/`, `/boot`
a `/nix`. Zaplnění od 90 % je varování, od 98 % chyba.
Bez sestavení nepotvrzuje shodu s receptem; tu ověřuje `verify`.
[Příkazy a návratové kódy](cli.md#výstupy-a-návratové-kódy).

`cleanup` pouze čte. Chrání aktivní, nabootovaný a výchozí systém i nejvyšší
dochovanou generaci odlišnou od aktivní jako možnost návratu.
`review` znamená ruční posouzení, `unknown` potřebu dalších informací.
Součet `closureBytes` není odhad uvolnitelného místa: závislosti se sdílejí.

```sh
nixberry cleanup vm-demo-libvirt
nixberry cleanup --artifact-root /cesta/k/balickum --json
```

Inventura respektuje rozsah hypervizoru a vyhrazeného úložiště.
Vypnuté VMware VM mimo něj nejsou úplně inventarizované; fyzická alokace
thin úložiště může zůstat neznámá. Neznámá velikost je `null`, nikoli nula.
`--artifact-root` lze opakovat; odkazy na adresáře se nepřecházejí a exporty
s manifestem jsou chráněné. Použití obrazu mimo sledovaný rozsah nelze vyloučit.

<a id="pamet"></a>

## Paměť a swap

[Modul swapu](../modules/system/swap.nix) nastavuje zram s `zstd`,
limitem nejvýše 2 GiB a nejvýše velikosti RAM, prioritou 100.
Záložní `/swapfile` má 8 GiB a prioritu 10.

Na cílovém serveru ověř:

```sh
free -h
grep -E '^(MemTotal|MemFree|MemAvailable|Cached|SwapTotal|SwapFree):' /proc/meminfo
cat /proc/pressure/memory
swapon --show
zramctl
journalctl -b -u mkswap-swapfile.service
```

Vytváření swap souboru při prvním startu může naplnit souborovou cache,
kterou hypervizor započítá do obsazené RAM. Rozlišuj `MemFree`, `Cached`
a `MemAvailable`. Vysoká cache s dostatečným `MemAvailable` a bez tlaku
na paměť sama nepotvrzuje nedostatek RAM.

## Společné aplikace

Midnight Commander používá připnutý vstup `mc-modern`. Samotný vstup aktualizuje
`nix flake update mc-modern --flake .`; server potom aktualizuj běžným CLI.
Uživatelské `~/.config/mc/ini` má přednost před výchozím nastavením balíčku.
Bash wrapper `mc` zachovává poslední adresář; přímá binárka tuto funkci nemá.
Schránka Wayland vyžaduje grafickou relaci, kterou samotné SSH neposkytuje.

HSTR je v [modules/applications/hstr/](../modules/applications/hstr/package.nix).
V interaktivním Bashi `Ctrl+R` otevře historii a výběr vloží do příkazové
řádky; Enter jej spustí. Historie se před otevřením synchronizuje.

Kernel a firmware RPi určuje připnutý `nixos-raspberrypi` a
[platforms/rpi/boot.nix](../platforms/rpi/boot.nix), nikoli aplikační vstup `unstable`.

<a id="media"></a>

## Nix výstupy bez nasazení

Z kořene receptu, pro existující místní deklaraci:

```sh
nix build --no-link --print-out-paths --no-write-lock-file path:.#nixberrySystems.vm-demo-libvirt.config.system.build.toplevel
nix build --no-link --print-out-paths --no-write-lock-file path:.#nixberryImages.vm-demo-libvirt
nix build --no-link --print-out-paths --no-write-lock-file path:.#nixberryInstallers.vm-demo-libvirt
```

`nixberryImages.CÍL` vybírá disk konkrétní VM nebo plnou SD RPi.
`nixberryInstallers.CÍL` vybírá ISO VM; médium je pod `iso/*.iso`.

`nixberryTools.CÍL` respektuje deklarovanou metodu.
`nixberryToolsByMethod.image.CÍL` a `.iso.CÍL` vybírají variantu výslovně.
Každý online VM backend poskytuje `probe/create/install/provision/reset`.
Libvirt používá `nixberry-vm-*` a `nixberry-libvirt-probe`,
ostatní `nixberry-BACKEND-*`. Samostatné `.reset` nepotřebuje systém ani médium.
Manifest rozlišuje `imagePath` a `isoPath`; nepoužitá položka je `null`.

Generické obrazy mají 32 GiB a výchozí hostname profilu:

| Platforma | Diskový balíček | ISO balíček |
| --- | --- | --- |
| libvirt/QEMU | `nixberry-qemu-image` | `nixberry-installer` |
| Hyper-V | `nixberry-hyperv-image` | `nixberry-hyperv-installer` |
| Proxmox | `nixberry-proxmox-image` | `nixberry-proxmox-installer` |
| VirtualBox | `nixberry-virtualbox-image` | `nixberry-virtualbox-installer` |
| VMware | `nixberry-vmware-image` | `nixberry-vmware-installer` |

RPi poskytuje `nixberry-rpi-installer` a síťovou variantu
`nixberry-rpi-network-installer`. Pro předání konkrétního stroje použij `export`,
který přibalí odpovídající médium, deklaraci, lock a návod.

## Testování a údržba dokumentace

[Testovací postup](test-plan.md) rozlišuje izolované kontroly, sestavení,
živé nasazení a ověření hostovaného systému. Výsledek jedné vrstvy nepotvrzuje ostatní.

Verzi určuje [VERSION](../VERSION); `nixberry --version` ji vypisuje.
Číslo verze nenahrazuje lock ani přesnou systémovou cestu.

[README](../README.md) je jediný vstupní návod. Podrobnosti jsou v `docs/`.
Popisuj aktuální příkazy, podporované varianty a omezení podle zdrojů.
Příklady VM pojmenovávej `vm-demo-*`; skutečnou evidenci ukazuje `list`.
