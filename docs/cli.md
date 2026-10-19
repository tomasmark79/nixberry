# Reference CLI

[← Hlavní návod](../README.md)

`nixberry [PŘÍKAZ] [CÍL] [VOLBY]` pracuje s místními deklaracemi.
Bez příkazu vypíše `list`. Cíl je jméno deklarace, nikoli IP adresa.
Příkaz za `--` lze předat pouze do `ssh`.

## Evidence a správa

| Příkaz | Výsledek |
| --- | --- |
| `list` | Místní cíle, konfigurace a možnosti správy. |
| `show CÍL` | Deklarace, síť, prostředky, metoda a dostupné operace. |
| `status [CÍL]` | Čerstvý stav jednoho nebo všech cílů, bez cache. |
| `ssh CÍL [-- PŘÍKAZ …]` | Interaktivní přihlášení nebo vzdálený příkaz. |
| `probe CÍL` | Podrobný průzkum hostitele/platformy; RPi má vlastní sondu. |
| `doctor [CÍL]` | Provozní diagnostika bez sestavení cílového systému. |
| `verify CÍL` | Kontrola identity, instalace, služeb a shody s receptem. |
| `cleanup [CÍL]` | Náhled generací, VM a obrazů; nic nemaže. |

Bez cíle se `status`, `doctor` a `cleanup` týkají celé evidence.
`list`, `show` a čtecí sondy nepotřebují sestavovat systém ani média.
`verify` vyhodnotí očekávaný systém. První spuštění může sestavit samotné nástroje.

## Aktualizace

| Příkaz | Výsledek |
| --- | --- |
| `diff CÍL` | Porovná běžící systém s receptem; může sestavovat místně a stahovat části systému. |
| `diffg CÍL` | Na serveru porovná dvě nejvyšší dochované generace. |
| `update CÍL` | Vypíše plán bez připojení a sestavení. |
| `update CÍL --apply` | Sestaví, přenese a aktivuje systém, potom ověří výsledek. |

`diff` a `diffg` server nemění. U obou `--dry-run` vynechá připojení a sestavení.
Systémové porovnání neporovnává data aplikací. Po rollbacku nemusí být nejvyšší
generace aktivní; `diffg` vypisuje aktivní systém zvlášť.

Aktualizace používá aktuální `flake.lock`, nemění závislosti ani oddíly
a automaticky nerestartuje zařízení. Při přerušení zjisti stav jednotky
`nixberry-update` a ověř server před opakováním.

## Nové deklarace VM

| Příkaz | Backend |
| --- | --- |
| `new-libvirt-vm JMÉNO` | libvirt |
| `new-hyperv-vm JMÉNO` | Hyper-V |
| `new-proxmox-vm JMÉNO` | Proxmox |
| `new-virtualbox-vm JMÉNO` | VirtualBox |
| `new-vmware-vm JMÉNO` | VMware Workstation |

Každý příkaz vytvoří místní `instances/JMÉNO.nix`; VM ještě nevytváří.
Jméno má 1–40 malých písmen, číslic a pomlček, začíná písmenem a končí
písmenem nebo číslicí. Existující deklarace se nepřepisuje.

- `--host HOSTITEL` vybírá z místního `hosts.local.nix` přes loader `hosts.nix`; jediný vhodný hostitel se vybere automaticky.
- `--offline` vytvoří deklaraci bez hypervizoru. Nelze jej kombinovat s `--host`.
- `--configuration JMÉNO` vybírá modul z `configurations/`, výchozí je `default`.
- `--method image|iso` ukládá způsob nasazení; výchozí je `image`.
- `--dry-run` vypíše návrh bez vytvoření souboru.

Názvy `new-libvirt`, `new-hyperv`, `new-proxmox`, `new-virtualbox`
a `new-vmware` jsou aliasy odpovídajících příkazů s příponou `-vm`.

## Nasazení a odstranění

| Příkaz | Výsledek |
| --- | --- |
| `plan CÍL` | Plán vytvoření VM nebo obnovy RPi. |
| `create CÍL --apply` | Vytvoření nové VM podle metody. |
| `install CÍL --apply` | Dokončení nasazení vytvořené VM podle stejné metody. |
| `provision CÍL --apply` | Vytvoření a dokončení VM. |
| `reproduce CÍL --apply` | Úplné nasazení VM nebo obnova RPi. |
| `reset CÍL --apply` | Odstranění povolené spravované VM po bezpečnostních kontrolách. |

Bez `--apply` tyto operace pouze plánují; příprava nástrojů a médií může
sestavovat nebo stahovat závislosti. `plan` nepřijímá `--apply`.

`image` vytvoří vypnutou VM s hotovým diskem; `install` ji spustí a ověří.
`iso` vytvoří prázdný disk a spustí instalátor; `install` provede instalaci,
korektní vypnutí, odpojení ISO a kontrolu startu z disku.

`reset` vyžaduje `allowReset = true;` v deklaraci VM a deklarovaného hostitele.
Bez `--apply` jen ověřuje plán, nepotřebuje systém ani médium.
Offline VM a RPi jej nepodporují. [Rozsah odstranění](vm.md#reset).

## Export

```sh
nixberry export vm-demo-libvirt --output ~/vm-demo-libvirt-disk
nixberry export vm-demo-libvirt --method iso --output ~/vm-demo-libvirt-iso
nixberry export nixberry --output ~/nixberry-sd
```

`export` běžně sestavuje a zapisuje místní balíček. `--dry-run` nic nesestavuje
ani nezapisuje; `--apply` zde neplatí. Adresář musí být nový a mimo recept.
Relativní cesta se počítá od aktuálního pracovního adresáře.
[Obsah balíčku a předání](offline.md).

## Společné volby

| Volba | Kde platí |
| --- | --- |
| `--help`, `--version` | Nápověda a verze nástroje. |
| `--method image|iso` | VM: `new-*`, `show`, `plan`, `create`, `install`, `provision`, `reproduce`, `export`. |
| `--configuration JMÉNO` | Pouze `new-*`; u existujícího cíle uprav deklaraci. |
| `--offline` | Pouze `new-*` bez `--host`. |
| `--ssh root@HOST[:PORT]` | Offline VM: `status`, `ssh`, `doctor`, `verify`, `update`, `diff`, `diffg`. |
| `--host` | Hostitel pro `new-*`; u přímé správy offline VM je alternativou k `--ssh`. |
| `--output ADRESÁŘ` | Pouze `export`. |
| `--artifact-root ADRESÁŘ` | Pouze `cleanup`; lze opakovat. |
| `--address IP_SD`, `--erase-existing` | Pouze `plan` a `reproduce` cíle RPi. |
| `--json` | `list`, `show`, `status`, `verify`, `doctor`, `cleanup`, `export`. |

`--apply` a `--dry-run` jsou vzájemně výlučné.
`--dry-run` u `doctor` a `cleanup` nevynechává jejich čtecí dotazy.
`ssh` ani `list` tento přepínač nepřijímají.
`--ssh` a `--host` nelze kombinovat. Přímé SSH nemění síť uvnitř obrazu.

## Výstupy a návratové kódy

Data a nápověda chodí na stdout, vlastní průběžné hlášky a diagnostika na stderr.
`--json` zachovává platný JSON bez informačních příměsí. Barvy se řídí proudem,
`NO_COLOR` a `TERM=dumb`; cizí výstupy se nepřekládají.

`doctor` vrací `0` bez nálezu, `1` při chybě a `2` při samotném varování.
Neúplná inventura `cleanup` vrací `1` a zachovává částečné výsledky.
Nenulový kód vyhodnocuj spolu s hláškou a známým stavem operace;
neznamená, že předchozí zápis byl automaticky vrácen.
