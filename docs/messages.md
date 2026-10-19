# Psaní uživatelských hlášek

Společná vrstva `management/deployment/messages.py` odděluje text, překlad
a zobrazení. Používá ji společné CLI, diagnostika, offline export, adaptéry
hypervizorů i instalace a obnova RPi. Závazná pravidla pro další práci jsou
v [AGENTS.md](../AGENTS.md).

## Zápis v Pythonu

```python
from messages import _, info, success, warning

info(_("Přenáším systém na {host}…"), host=destination)
success(_("Systém je aktualizovaný a ověřený."))
warning(_("Nový kernel se použije po restartu."))
```

Text ponech přímo u kódu, který vysvětluje. Celou větu označ `_()`;
nepoužívej v něm f-string ani skládání z přeložených fragmentů. Pojmenované
hodnoty se doplní až po překladu. Příkazy, cesty a technická rozhraní ponech
v původním tvaru. U počtů preferuj neutrální popisek `Počet cílů: {count}`;
pokud je nutné skloňování, doplň podporu `ngettext` a odpovídající testy.

Pro výjimky přelož a zformátuj větu při vytvoření, ale nevypisuj ji současně:

```python
raise RuntimeError(_("Přenos cíle {name} se nezdařil. Spusť {command}.").format(
    name=name, command=f"nixberry status {name}"))
```

Společný vstup vypíše chybu přes `error()` jednou a skončí nenulovým kódem.
Výjimku po částečně dokončené operaci formuluj přesně: například aktivace
mohla proběhnout, i když SSH nevrátilo potvrzení. Nepřidávej tvrzení, které
kontroly neprokázaly. Text cizí výjimky vkládej jako hodnotu `{error}`, aby se
jeho složené závorky nezpracovávaly jako naše formátovací značky.

## Význam a vedení uživatele

- `info`: právě prováděný krok, plán, kontext nebo další příkaz.
- `success`: ověřený výsledek, nikdy pouhý začátek operace.
- `warning`: důležitý stav vyžadující pozornost, například potřeba restartu.
- `error`: neúspěch; pokud je to známé, vysvětli stav operace a další krok.

Piš přirozené české věty a pro stejnou situaci používej stejná slova.
Příkaz k nápravě musí používat skutečný cíl a zachovat případné `--ssh`.
Neúspěšná následná kontrola nesmí skončit hláškou o ověřeném úspěchu.

## Proudy, barvy a podřízené nástroje

Vlastní hlášky mají označení `[nixberry]`, míří na stderr a ihned se flushují,
aby se zobrazily před spuštěním dalšího nástroje. Datové výsledky patří
na stdout. V režimu `--json` patří na stdout pouze JSON; nepřeváděj tabulky,
JSON klíče ani technické hodnoty na hlášky.

Označení používá tyrkysovou pro průběh, zelenou pro úspěch, žlutou pro
upozornění a červenou pro chybu. Věta musí být srozumitelná i bez barvy.
O zapnutí rozhoduje terminál skutečného cílového proudu, nikoli jiného
proudu. `NO_COLOR` i s prázdnou hodnotou a `TERM=dumb` barvy vypínají.

Výstup Nixu, SSH, `nvd` a dalších nástrojů nepřepisuj, nepřekládej a nepřidávej
k němu automaticky naše označení. Při spouštění nástroje v JSON operaci ověř,
že jeho výstup neznečistí stdout s JSON výsledkem. Přesměrování našich hlášek
na stderr samo o sobě výstupy podprocesů nepřesměruje.

`diffg` překládá a formátuje vlastní nadpisy místně, bezpečně je vloží do
vzdáleného shellového příkazu a vypíše je na vzdálený stderr. Barvy `nvd`
volí samostatně podle místního stdout; na serveru není potřeba Python helper
ani překladový katalog. Bash skripty používají `messages.sh` a zapisují například
`nb_info 'Cíl: %s' "$name"`; helper je jen soubor funkcí Bashe a nespouští
Python pro jednotlivé řádky. `nb_help` vypisuje nápovědu na stdout.

Celý shellový skript připraví místně `shell_script(path)`: přeloží konstantní
hlášky a přiloží helper s volbou barev podle místního stderr. U místního
libvirt `create` totéž provede `shell_runner.py` jednou před spuštěním Bashe.
Instalační skripty se stejným způsobem předávají přes stdin do SSH i místní
obnovy RPi. Neměň zástupné printf značky a dynamické hodnoty nikdy nevkládej
do formátovacího řetězce.

`localized_script(path)` překládá chyby PowerShellu z posledního literal
argumentu `Require` a z literal `throw`. Překlady bezpečně cituje pro
PowerShell, zatímco podmínky a technické hodnoty ponechává stejné.
`python_script(path)` přibalí RPC adaptéru společný modul a případný binární
katalog; adaptér proto zachová JSON i na hostiteli bez projektu nixberry.

| Část | Podoba výstupu |
| --- | --- |
| `list`, `show`, `status`, `doctor`, `cleanup` | Datové přehledy na stdout, rady a chyby na stderr |
| `new-*` | Náhled deklarace nebo cesta na stdout, potvrzení a další krok na stderr |
| `verify`, `export` | Výsledek/cesta nebo JSON na stdout, průběh a potvrzení na stderr |
| `ssh`, `probe`, `plan`, nasazovací adaptéry | Převzaté technické výstupy a data zachované; vlastní hlášky na stderr |
| libvirt, Hyper-V, Proxmox, VirtualBox, VMware | Společný Python helper; RPC zachovává své technické schéma |
| RPi instalace, místní obnova a recovery test | Společný Python a Bash helper, sondy zůstávají datové |
| Kontroly obrazů a evidence | Diagnostika na stderr, případné strukturované výsledky na stdout |

Překladový katalog nezahrnuje zdrojové komentáře, generované Nix deklarace
a čisté Nix výrazy. Ty nejsou runtime hláškami CLI.
Chyby Nix vyhodnocení a původní texty cizích nástrojů zůstávají jejich vlastní
diagnostikou. Nix balíčky používají `management/messages.nix`; symbolické
odkazy `messages.py` v adaptérech zpřístupňují tentýž modul při spuštění ze zdrojů.

## Překlady a ověření

Čeština je výchozí bez ohledu na `LANG` a `LC_ALL`.
`NIXBERRY_LANG=en` je připravené pro anglický katalog; chybějící překlad
zůstane česky. Anglický katalog není součástí projektu.
Jazyk ostatních nástrojů se tím nemění. Datové JSON klíče, stavové kódy,
identifikátory kontrol a hodnoty z technických nástrojů se nepřekládají;
textová pole jako `message`, `hint` a `note` překládat lze.

Úplnou šablonu vytvoř z kořene receptu:

```sh
python3 management/deployment/extract_messages.py > management/deployment/locales/nixberry.pot
```

Tento nástroj zpracuje označené texty Pythonu, Bashe
i PowerShellu. `test_message_contract.py` kontroluje aktuálnost šablony
a odmítá nové přímé textové printy či interpolované shellové hlášky.

Překlad ulož jako `management/deployment/locales/en.po` a zkompiluj přes
`msgfmt --check-format` do `en/LC_MESSAGES/nixberry.mo` v témže adresáři.
Při přidání katalogu zapoj kompilaci do Nix sestavení. Balíčky přebírají
binární katalog spolu se společným modulem; POT není runtime součást.
Bash a PowerShell se překládají místně před spuštěním nebo přenosem,
RPC Python dostává modul i katalog v jednom přenosu. Vzdálený host
nepotřebuje gettext a český katalog není nutný.

Při úpravě ověř terminál, přesměrovaný stdout a stderr, `NO_COLOR`,
`TERM=dumb`, chybějící katalog a případný JSON režim. Zkontroluj také
chybovou cestu: nesmí oznámit úspěch po neověřeném výsledku a musí zachovat
návratový kód podřízeného nástroje.
