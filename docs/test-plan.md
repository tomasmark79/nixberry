# Testování NixBerry

[← Hlavní návod](../README.md) · [Společný kontrakt](platform-consistency.md)

Testování rozlišuje statickou kontrolu, izolované regresní testy, sestavení,
živé nasazení a ověření běžícího systému. Sestavený obraz nepotvrzuje boot;
úspěch mocku nepotvrzuje skutečný hypervizor.

## Izolované kontroly

Z kořene receptu:

```sh
python3 -m unittest discover -s management/deployment -p 'test_*.py'
python3 -m unittest discover -s management/libvirt -p 'test_*.py'
python3 -m unittest discover -s management/hyperv -p 'test_*.py'
python3 -m unittest discover -s management/rpi -p 'test_*.py'
python3 management/deployment/check_inventory.py
```

Testy používají dočasné deklarace a náhrady transportů. Některé spouštějí
Nix s malými testovacími artefakty a ověřují skutečný přenos mezi Python
procesy. Nepotřebují reinstalaci běžícího serveru.

| Oblast | Kontroly v `management/deployment/` |
| --- | --- |
| CLI, dostupné operace a odmítnuté kombinace voleb | `test_cli.py`, `test_platform_contract.py` |
| Konfigurace a deklarace všech backendů | `test_configurations.py`, `test_scaffold.py`, `test_methods.py` |
| Balíčky nástrojů, manifesty image/ISO a reset bez média | `test_platform_contract.py` |
| Přenosný adaptér, identity, disky a přenos dat | `test_portable.py` |
| Hyper-V výběr a přenos média | `test_hyperv_media.py` a `management/hyperv/test_*.py` |
| Reset a odmítnuté nebezpečné kroky | `test_reset.py` a backendové kontroly |
| Doplňování a aliasy, online/offline a přímé SSH | `test_completion.py` |
| Výstupy, diagnostika, barvy a katalog | `test_diagnostics.py`, `test_messages.py`, `test_message_contract.py` |
| Izolace, evidence a řízení kampaně | `test_campaign.py` |

Shellovou integraci Nixonu ověřuje z kořene notebookového projektu
`nix build --no-link path:.#checks.x86_64-linux.nixberry-shell`.
Používá náhrady Nixu, editoru a nasazení, včetně vygenerovaného spouštěče a loaderu.

## Kontrola hotových médií

Pro obrazy QCOW2, VDI a VMDK, s dostupnými `qemu-img` a mtools:

```sh
python3 management/deployment/check_images.py /cesta/system.qcow2 /cesta/system.vdi /cesta/system.vmdk
```

Kontrola čte formát, kapacitu, GPT, ext4, UEFI fallback a bootovací záznam
s kernelem, initrd a systémovou cestou. Obrazy nepřipojuje ani nemění.
Nenahrazuje skutečný start.

Samostatné aplikační testy:

```sh
nix build --no-link path:.#mc
nix run path:.#mc-tests
nix build --no-link path:.#hstr path:.#packages.x86_64-linux.hstr.tests.scroll
```

MC testy vyžadují `unshare -Ur` a používají TUI v tmuxu. HSTR používá
dočasnou historii. Pro ARM balíčky vyber příslušnou architekturu.

## Matice živých scénářů

Pro každou podporovanou kombinaci hostitele a VM backendu prověř obě metody.
Windows používá SSH; místní libvirt, VirtualBox a VMware běží na Linuxu.

| Skupina | Co ověřit |
| --- | --- |
| ON | Nová deklarace, plán, nasazení, start z disku, SSH, `doctor`, `verify`, aktualizace, generace a úklid. |
| OFF | Export image/ISO každého backendu a plné SD RPi; manifest, recept, návod, kontrolní součty. |
| HAND | Skutečný ruční import disku nebo instalace z exportovaného ISO, potom správa přes SSH. |
| NET | DHCP i pevná IP, brána, DNS a dosažitelnost ve vyhrazené síti. |
| NATIVE | Existující server a RPi pouze čtecími kontrolami. |

U ON střídej vstupy `reproduce`, `provision` a oddělené `create/install`.
Ověř také přepsání deklarované metody přes `--method` a výběr konfigurace.

Každý případ musí doložit:

1. Nepřítomný cíl a vlastní vyhrazené prostředky před vytvořením.
2. Správný formát, kapacitu, UEFI, řadič, síť a guest agenta.
3. U image hotový disk bez formátování; u ISO prázdný disk a instalační prostředí.
4. Start přesné systémové cesty z disku bez připojeného ISO.
5. SSH, identitu, síť, služby, `doctor` a `verify`.
6. `diff` před aktualizací, plán bez zápisu, aktivaci a `diffg` po ní.
7. Odmítnutí opakovaného vytvoření existující VM.
8. Náhled úklidu a bezpečné odstranění jen vlastních testovacích prostředků.

U RPi samostatně ověř obnovu ze SD, identitu SSD, start bez karty a běžnou
aktualizaci. Živý zápis vyžaduje určený cíl a výslovně povolený rozsah.
Samotná dostupnost serveru nebo RPi není pokyn k aktualizaci či obnově.

## Chybové a bezpečnostní případy

- Kolize jména, UUID, MAC, VMID a cizí owner musí zastavit zápis.
- Nesprávný hostitel, disk, součet, profil nebo kapacita nesmí vést k instalaci.
- Pro reset ověř snapshoty, podkladové obrazy, sdílení, odkazy a změnu identity
  mezi plánem a odstraněním. Bez oprávnění ani bez `--apply` nesmí mazat.
- Nekorektní vypnutí nesmí přejít na tvrdé vypnutí; neúspěšná ISO instalace
  nesmí předčasně odpojit médium nebo restartovat do neověřeného systému.
- Nedostupné SSH, chybějící nástroj a guest agent musí skončit ohraničeně
  s použitelnou diagnostikou.
- Přerušení přenosu nebo ztráta odpovědi nesmí slepě opakovat vytvoření.
- Ověř stdout/stderr, JSON, návratový kód, TTY, `NO_COLOR`, `TERM=dumb`
  a chybějící překlad.

## Runner kampaně

[management/deployment/campaign.py](../management/deployment/campaign.py)
bez `--apply` vypíše matici. Živý běh vyžaduje připravené hostitele,
vyhrazené prostředky a konkrétní nastavení:

```sh
python3 management/deployment/campaign.py
python3 management/deployment/campaign.py --apply \
  --run-root /home/shared/tmp/nixberry-campaign-ID \
  --settings /home/shared/tmp/nixberry-campaign-settings.json
```

`--case ID` vybírá případ, `--resume` pokračuje v existujícím reportu.
Nastavení může obsahovat `hostOverrides` a vyhrazené `networks`.
Podporovaný vnořený hostitel je popsaný v [návodu Proxmox labu](proxmox-lab.md).

Runner pracuje se soukromou kopií receptu a výslovně ji připíná, aby ji
nepřesměrovalo prostředí shellu. Výsledky a logy ukládá mimo zdrojový projekt.
Používá vlastní jména `vm-demo-test-*`, UUID a evidenci prostředků.

Online VM uklízí veřejným `reset`: ve vlastní deklaraci povolí odstranění,
zkontroluje plán a pak předá `--apply`. HAND import se ověřuje proti evidenci
ručního vytvoření; u VMware má vlastní interní úklid, protože import nemá
marker automatické správy. Nejisté zbytky zachová k posouzení.

## Evidence výsledků

Pro každý běh ulož revizi receptu, deklaraci a konfiguraci, hostitele, identitu
VM, metodu, očekávaný systém, příkazy, návratové kódy a oba výstupní proudy.
Nekopíruj tajné klíče, hesla ani tokeny. Zaznamenej zvlášť sestavení,
aktivaci, boot a živé kontroly.

Výsledky patří do adresáře běhu, nikoli do uživatelské dokumentace.
Runner používá stavy `NOT_RUN`, `RUNNING`, `PASS`, `FAIL` a `BLOCKED`.
U neúspěšného nebo blokovaného případu uveď konkrétní důvod.
Nevyzkoušený scénář neoznačuj za úspěšný.
Při nejasné identitě nebo částečném zápisu zastav další zápisy a nejprve
ověř stav; úspěšné případy se při pokračování automaticky neopakují.
