# Obnova Raspberry Pi 5 ze SD

[← Hlavní návod](../README.md) · [Offline balíček](offline.md)

Postup obnovuje SSD z plné SD. Samotný start SD nic nemaže.
Příkazy mají vždy uvedeno, zda běží na správcovském počítači, nebo na RPi.

<a id="obnova-rpi-1-ověřit-deklaraci-a-připravit-sd--nixon"></a>

## 1. Ověřit deklaraci a připravit SD — správcovský počítač

Nejprve zkopíruj [šablonu RPi](../management/examples/rpi.nix) do
`management/rpi/targets/rpi-demo.nix`. Uprav ji podle skutečného zařízení;
šablona obsahuje smyšlené identifikátory. Připrav také vlastní `admin.pub`.

Název souboru bez `.nix` určuje cíl příkazů, například `nixberry show rpi-demo`.
`hostName` určuje hostname systému. Při jeho změně zachovej `bootLabel` a
`rootLabel` podle existujících oddílů; bez výslovného nastavení se odvozují
z `hostName`. `rootLabel` má limit 16 znaků pro ext4. Běžný `update` oddíly
nepřejmenovává. Jejich označení ověříš příkazem
`nixberry ssh rpi-demo -- lsblk -o NAME,FSTYPE,LABEL,PARTLABEL,MOUNTPOINTS`.

| Údaj | Příklad; dosaď skutečné hodnoty |
| --- | --- |
| RPi serial | `0000000000000000` |
| LAN MAC | `02:00:00:00:00:20` |
| Adresa systému na SSD | `192.168.50.20/24` |
| SSD serial | `EXAMPLE_SERIAL` |
| SSD by-id | `/dev/disk/by-id/ata-EXAMPLE_EXAMPLE_SERIAL` |

Jiné RPi nebo SSD potřebuje vlastní deklaraci a nové médium.
Firmware musí umožňovat USB start; EEPROM nástroj nenastavuje.

Exportuj současný recept z kořene projektu:

```sh
nixberry export rpi-demo --output ~/nixberry-rpi-offline
```

Velikost rozbaleného obrazu se mění s konfigurací. Zjisti ji pomocí `zstd -l`
a porovnej s kapacitou karty z `lsblk -b`.

Zápis smaže kartu. Identifikuj ji podle kapacity, čtečky a oddílů,
odpoj její oddíly a zapiš **celý rozbalený obraz**. Dokonči `fsync`,
vyprázdni diskovou cache a porovnej všechny zapsané bajty s obrazem;
potom kartu bezpečně odpoj. Původní funkční instalační SD zachovej.
Pevný příklad typu `/dev/sda` není bezpečný výběr zařízení.

<a id="obnova-rpi-2-spustit-sd-a-obnovit-ssd--rpi"></a>

## 2. Spustit SD a obnovit SSD — RPi

Vypni RPi, vlož SD a zapni ho. Pokud firmware preferuje SSD, nech SSD
při startu odpojené a připoj ho až v instalačním prostředí.
`findmnt /` musí ukazovat SD, obvykle `/dev/mmcblk0p2`.

Na konzoli **RPi běžícího ze SD**:

```sh
sudo nixberry-install-local --target rpi-demo --dry-run --erase-existing
sudo nixberry-install-local --target rpi-demo --apply --erase-existing
```

Nejprve musí projít první příkaz. Druhý **smaže ověřený SSD** a nainstaluje
systém z místního store bez internetu. `--target` je povinný i přímo na RPi.
Kontroluje se model, serial, MAC, SSD, kapacita, manifest a připojené oddíly.
Připojený systémový SSD nelze přepsat; `--erase-existing` ochranu neobchází.

<a id="obnova-rpi-alternativa-řídit-stejnou-obnovu-z-nixonu-přes-ssh"></a>

### Alternativa: řídit stejnou obnovu ze správcovského počítače přes SSH

Zjisti aktuální DHCP adresu SD a dosaď ji za `IP_SD`:

```sh
nixberry plan rpi-demo --address IP_SD --erase-existing
nixberry reproduce rpi-demo --address IP_SD --erase-existing --apply
```

Používají se stejné kontroly i instalační skript.
Adresa SD nemusí být budoucí adresa systému na SSD.

<a id="obnova-rpi-3-dokončit-startem-z-ssd"></a>

## 3. Dokončit startem z SSD

Instalátor automaticky nerestartuje. Na RPi spusť `sudo poweroff`,
po vypnutí vyjmi SD a zapni ho s připojeným SSD. **Na správcovském počítači** ověř:

```sh
nixberry verify rpi-demo
```

Přihlášení a nové SSH host klíče řeší [provozní návod](../README.md#potize).
Heslo konzole instalovaného systému není nastavené.
Úspěšný výsledek zahrnuje systém z receptu, kořen na správném SSD
a aktivní SSH a networkd bez selhaných jednotek.

<a id="obnova-rpi-technické-poznámky"></a>

## Technické poznámky

Instalace používá připnuté disko a `nixos-install --system`.
`nixberry-rpi-install --stage` pouze přenáší store bez změny oddílů.
Menší `nixberry-rpi-network-installer` vyžaduje přenos cílového store.
Pro místní offline obnovu použij plnou SD. Rozsah kontrol a oddělení
sestavení od živého startu popisuje [testovací postup](test-plan.md).
