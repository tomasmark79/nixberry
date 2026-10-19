# Pravidla vývoje nixberry

## Samostatný repozitář

- Dokumentaci a komentáře piš česky; technické názvy nepřekládej. Po dokončeném a přiměřeně ověřeném kroku vytvoř lokální commit pouze souvisejících změn. Push vyžaduje výslovný pokyn.
- `hosts.local.nix`, `admin.pub`, konkrétní deklarace VM i RPi jsou místní data ignorovaná Gitem. Zálohuj je samostatně; do veřejných příkladů patří pouze smyšlené identity. Žádné změny živých strojů nevyplývají z přípravy zveřejnění.
- Nixon je samostatný spotřebitel místního spouštěče; nezaváděj zpětnou závislost na jeho checkoutu ani společný lock.

## Dokumentace

- V přehledech platforem používej názvy QEMU/KVM (libvirt), Hyper-V, Proxmox VE, VirtualBox, VMware Workstation a Raspberry Pi 5. Technické identifikátory backendů a příkazy zůstávají `libvirt`, `hyperv`, `proxmox`, `virtualbox`, `vmware` a `rpi`.

- Jediný vstupní uživatelský návod je kořenový `README.md`; podrobnosti patří do `docs/`. `AGENTS.md` je interní pravidlo pro práci na projektu. Udržuj aktuální postupy podle CLI a zdrojů, jednotné názvy příkazů, ukázkové VM `vm-demo-*` a platné odkazy včetně kotev. Nepřidávej kroniku úprav, datované výsledky, archiv, roadmapu ani diagramy. Logy a výsledky testů ukládej mimo uživatelskou dokumentaci; testovací návod má popisovat způsob ověření a jeho hranice.

## Jednotnost platforem

- Konkrétní deklarace VM v `instances/` jsou místní data a do Gitu nepatří. Verzuj tam pouze `.gitignore` a společný loader `default.nix`; příklady drž v dokumentaci. Při odebrání deklarace ze sledování zachovej místní soubor. Spouštěč a export používají `path:` recept, aby zahrnuly také ignorované deklarace; nevyžaduj `git add` pro zpřístupnění cíle.

- Při každé změně proveď také statickou kontrolu souvisejícího chování napříč `libvirt`, `hyperv`, `proxmox`, `virtualbox`, `vmware` a RPi. Nestačí ověřit jen upravený adaptér. U každé oblasti rozliš společné chování, záměrnou výjimku a chybějící implementaci; záměrnou výjimku zdůvodni v dokumentaci. RPi není VM a nemusí podporovat její operace.
- Sleduj celou cestu funkce: deklarace hostitele a instance, výchozí hodnoty a validace, generátor `new-*-vm`, registr schopností a příkazů, CLI a aliasy, přímé nástroje, adaptér, exportní manifest a návod, nápověda a doplňování. U nasazení porovnej `image` i `iso`, online i `--offline`, místní i vzdálené prostředí tam, kde jsou podporované. Práva, ověřování identity, chování bez `--apply`, chybové stavy a oddělení stdout/stderr nesmějí být slabší jen kvůli jinému backendu.
- Před dokončením kroku zaznamenej, které platformy a varianty změna pokrývá, co bylo skutečně ověřeno a co zbývá. Nový rozdíl buď sjednoť v rámci změny, nebo jej výslovně eviduj s důvodem a dopadem; nedoplňuj chybějící variantu jen do nápovědy. Vzor a stav ověření jsou v [kontrole jednotnosti](docs/platform-consistency.md).
- Ověřování přizpůsob rozsahu změny a pokynu uživatele. Statická revize nenahrazuje test adaptéru ani živý průchod. Při změně společného kontraktu doplň přiměřenou kontrolu napříč backendy místo testu pouze jednoho z nich; odložené testy označ jako neprovedené. Kontrola jednotnosti sama neopravňuje k vytváření, reinstalaci, vypnutí ani odstranění VM.

## Uživatelské texty a výstupy

- Shellové vstupy používají společný `management/deployment/shell-path.sh`: `NIXBERRY_RECIPE` má přednost před adresářem projektového spouštěče. Projekt nezávisí na Nixonu ani na `NIXON_CONFIG_DIR`. Neplatnou výslovnou cestu nenahrazuj jinou kopií. Automatizace nad soukromým receptem jej výslovně připíná přes `NIXBERRY_RECIPE`, aby ji nepřesměrovalo prostředí interaktivního shellu. Doplňování aliasů předává implicitní argumenty do společné funkce CLI; neudržuj druhý seznam cílů nebo voleb.

- Pojmenované konfigurace drž v `configurations/` jako běžné NixOS moduly. Každý cíl vybírá jednu pomocí `configuration`; chybějící pole znamená `default`. `profile` dál označuje platformu. Konfigurace výslovně importují `modules/common.nix` a případně přidávají služby či balíčky; neimportují jiné konfigurace. Sdílené části patří do `modules/`, bootování, disky, ovladače a guest podpora do `platforms/`, identita, síť a prostředky do deklarace stroje. Platforma se připojuje automaticky podle cíle. Nepřidávej další lockfile ani samostatnou větev CLI pro jednotlivé konfigurace.
- Pokud projekt poskytuje použitelný flake balíček, využij jej namísto druhé místní definice sestavení; zdroj připni ve společném locku a slučitelné vstupy sdílej pomocí `follows`.
- `allowReset` je booleovské oprávnění jednotlivé VM v `instances/`, výchozí `false`; nepatří do hostitele ani do identity uložené při vytvoření VM. `reset` všech VM backendů funguje bez sestavení systému či média, bez `--apply` pouze čte. Každá zapisující cesta musí vyžadovat výslovné povolení, ověřit vlastníka, skutečnou identitu, disky, sdílení, snapshoty a odkazy; po korektním vypnutí vše znovu ověřit. Nepřecházej na tvrdé vypnutí ani nemaž nejisté zbytky. `cleanup` je pouze náhled a offline ani RPi cíle nesmějí získat reset.
- Výběr konfigurace musí platit pro VM i RPi, aktualizaci, image, ISO a offline export. `--configuration` slouží pouze k vytvoření deklarace; u existujícího cíle se mění uložené pole. Neznámé jméno nesmí tiše přejít na `default`. Ověř také nápovědu, completion, manifest exportu a zachování výchozích systémových cest. Testovací konfigurace pro živé zkoušky drž v oddělené kopii receptu.

- VM metody drž jednotné napříč backendy: `image` je výchozí, `iso` je volitelná instalace na ověřený prázdný disk. `deploymentMethod` v deklaraci určuje výchozí volbu, `--method iso|image` ji jednorázově přepisuje u nasazení a exportu. `--offline` je nezávislá příprava bez hypervizoru. RPi používá obnovovací SD.
- Při změně nasazení ověř obě metody v CLI, deklaracích, doplňování, plánu a offline balíčku. Změna metody nepovoluje převzetí ani reinstalaci existující VM. ISO se odpojuje až po úspěšné instalaci a korektním vypnutí; úspěch znamená také ověřený start cílového systému z disku.

- Dodržuj [konvence hlášek](docs/messages.md). Informuj o skutečném kroku a ověřeném výsledku, přiměřeně podrobně. Chyba má vysvětlit známý stav operace a použitelný další krok; neslibuj úspěch ani neprovedené změny.
- V Pythonu používej společný `management/deployment/messages.py`: `_()` pro celou českou větu a `info`, `success`, `warning`, `error` pro hlášky na stderr. Hodnoty dosazuj až po překladu; příkazy, cesty, datové klíče a technické hodnoty nepřekládej. Nevkládej f-string ani dynamicky složený text do `_()`.
- Datové výsledky, tabulky a nápověda patří na stdout bez našeho barevného označení. Překládej jejich lidské popisky. `--json` musí vracet platný JSON bez informačních příměsí; schéma a technické hodnoty se s jazykem nemění. Výstupy podřízených nástrojů nepřekládej ani automaticky neobaluj prefixem.
- Samostatné Python příkazy používají společný `ArgumentParser` a při chybě `error()` s nenulovým návratovým kódem. RPC procesy zachovávají svůj JSON protokol; chybu pro uživatele vypisuje volající, aby se neopakovala.
- Bash používá `nb_info`, `nb_success`, `nb_warning`, `nb_error`, případně `fail`; první argument je jeden konstantní text v jednoduchých uvozovkách. Hodnoty předávej přes printf zástupné značky a samostatné argumenty. `nb_help` vypisuje datovou nápovědu. Helper nikdy nespouští Python kvůli jednotlivým řádkům.
- PowerShell ponechává technické odpovědi datové; český literal chyby piš jako poslední argument `Require` nebo jako literal `throw`, v jednoduchých uvozovkách. Nepřekládej části podmínek či příkazů.
- Přenášené skripty připravuj přes `shell_script`, `localized_script` nebo `python_script`. Ty přibalí potřebnou podporu a přeloží texty místně; vzdálený host nemusí mít knihovnu či katalog. Nový samostatný Nix balíček musí zahrnout společné zdroje a správný `PYTHONPATH`.
- Barvy se řídí skutečným cílovým proudem, `NO_COLOR` a `TERM=dumb`; za SSH nerozhoduj podle vzdáleného TTY. Text musí dávat smysl také bez barev. Převzatý stderr při úspěchu nezahazuj a nepřesouvej do stdout.
- Výchozí jazyk je čeština, nezávislá na systémovém `LC_ALL`. Angličtina se přidává katalogem, nikoli přepisováním českých zdrojových vět. Generované deklarace a jejich zdrojové komentáře zůstávají česky; chyby čistého Nix vyhodnocení a cizí nástroje nepřekládej pomocí runtime helperu.
- Po změně textu obnov `management/deployment/locales/nixberry.pot` pomocí `python3 management/deployment/extract_messages.py > management/deployment/locales/nixberry.pot` z kořene receptu. Katalog zahrnuje Python, Bash i PowerShell. Nové typy výstupů zahrň do kontroly pokrytí.
- Ověř příslušné adaptéry, nápovědu, chybu, přesměrování obou proudů, JSON, TTY, `NO_COLOR`, `TERM=dumb` a chybějící překlad. Průchod instalačními a mazacími cestami testuj izolovaně; dostupnost běžící VM sama o sobě není pokyn k reinstalaci nebo smazání.
