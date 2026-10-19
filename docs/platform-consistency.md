# Společný kontrakt platforem

[← Hlavní návod](../README.md) · [CLI](cli.md) · [Testování](test-plan.md)

Stejný význam příkazů a deklarací platí napříč VM backendy.
Rozdíly vyplývající z hypervizoru jsou v [návodu platforem](vm.md).
Pravidla vývoje stanovuje [AGENTS.md](../AGENTS.md).

## Matice rozhraní

| Oblast | libvirt | Hyper-V | Proxmox | VirtualBox | VMware | RPi |
| --- | --- | --- | --- | --- | --- | --- |
| Pojmenovaná konfigurace | ano | ano | ano | ano | ano | ano |
| Image a ISO | ano | ano | ano | ano | ano | obnova ze SD |
| `new-*-vm` a `--offline` | ano | ano | ano | ano | ano | vlastní deklarace |
| Síť v deklaraci cíle | ano | ano | ano | ano | ano | ano |
| Export systému a receptu | ano | ano | ano | ano | ano | ano |
| Správa systému přes SSH | ano | ano | ano | ano | ano | ano |
| Online `probe/create/install/provision/reset` | ano | ano | ano | ano | ano | vlastní sonda a obnova |
| Výchozí `allowReset = false;` | ano | ano | ano | ano | ano | nepoužije se |

Tabulka popisuje rozhraní, nikoli potvrzení živého průchodu každou kombinací.
Offline VM získá přístup ke správě systému přes `access` nebo `--ssh`,
nikoli správu hypervizoru. `status` a `doctor` mohou také oznámit chybějící
nastavení přístupu.

## Pravidla společného chování

- Nové deklarace používají společný generátor. Kapacita, síť, konfigurace,
  metoda a oprávnění mají stejný význam pro všechny backendy.
- Výchozí metoda VM je `image`; `iso` je rovnocenná podporovaná volba.
  `--offline` je nezávislé na metodě.
- Přímé balíčky nástrojů mají stejnou sadu operací. Reset lze sestavit
  samostatně bez systému a média; oprávnění ani kontroly se tím neobcházejí.
- Manifest médií rozlišuje `imagePath` a `isoPath`, nepoužité pole je `null`.
- Data patří na stdout, diagnostika na stderr. Zachovává se také stderr
  úspěšných podprocesů, bez překladu a dodatečného označení.
- Doplňování vychází z aktuálních místních deklarací a dostupných operací,
  bez cache výsledků, sestavení systému nebo dotazů na hostitele.
- Konkrétní deklarace VM jsou místní data a v Gitu se nesledují.
  Spouštěč a export používají `path:` a zahrnují je i bez `git add`.

## Záměrné rozdíly

RPi má vlastní obnovu ze SD. Libvirt podporuje místní `qemu:///system`;
podporované systémy a transporty ostatních hostitelů určuje jejich adaptér.
Formát disku, řadič, guest agent a úložiště odpovídají konkrétnímu hypervizoru.

Hyper-V uchovává v owner záznamu pole `isoSha256` i pro hotový disk.
Je to uložená identita média; význam manifestových `imagePath` a `isoPath`
ani přenosového `mediaBytes` tím není změněn.

Ruční import nemá značky automatické správy a veřejný reset jej nepřebírá.
Testovací runner u HAND VMware ověřuje vlastní evidenci importu a používá
interní úklid. Online spravované VM uklízí přes veřejný reset.

## Kontrola při změně

Projdi celou související cestu: deklarace, výchozí hodnoty, validaci,
generátor, registr schopností, CLI a aliasy, adaptéry, export, nápovědu
a doplňování. Porovnej všech pět VM backendů, RPi a příslušné online/offline,
image/ISO i hostitelské varianty.

Rozliš implementaci, izolovaný test, sestavení a živé ověření.
Jeden úspěšný test nepotvrzuje ostatní platformy nebo všechny ochrany resetu.
Přehled regresních kontrol a živých scénářů je v [testovacím postupu](test-plan.md).
