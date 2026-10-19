"""Přenosný balíček hotového obrazu, připnutého receptu a kontrolních součtů."""

from messages import _, info, shell_script
from runtime import deployment_method

import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile


def export(recipe, target_name, target, destination, *, dry_run=False, method=None):
    recipe, destination = Path(recipe).resolve(), Path(destination).expanduser().resolve()
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", target_name):
        raise RuntimeError(_('Nepovolené jméno exportovaného cíle.'))
    if destination.exists() or destination.is_symlink():
        raise RuntimeError(_('Výstupní adresář už existuje; export jej nepřepíše.'))
    if destination == recipe or recipe in destination.parents:
        raise RuntimeError(_('Balíček ukládej mimo kořen projektového receptu nixberry.'))
    selected = method or deployment_method(target)
    attribute = ("nixberryInstallers." if selected == "iso" and target["backend"] != "rpi" else "nixberryImages.") + target_name
    command = ["nix", "build", "--print-out-paths", "--no-write-lock-file",
               "path:" + str(recipe) + "#" + attribute]
    if dry_run:
        return {"target": target_name, "destination": str(destination), "deploymentMethod": selected,
                "configuration": target.get("configuration", "default"),
                "allowReset": target.get("allowReset", False),
                "build": [*command[:-1], "--no-link", command[-1]]}
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging_directory = tempfile.TemporaryDirectory(prefix=".nixberry-export-", dir=destination.parent)
    except PermissionError as error:
        raise RuntimeError(_("Do umístění balíčku {path} nelze zapisovat. Relativní cesta se počítá od pracovního adresáře. Vyber zapisovatelný adresář, například --output ~/{target}-offline.").format(
            path=destination, target=target_name)) from error
    info(_("Připravuji offline balíček cíle {target} do {path}."), target=target_name, path=destination)
    with staging_directory as staging:
        # Nix pořídí jediný neměnný snímek zdrojů. Pracovní recept už dál nečteme.
        metadata = json.loads(subprocess.check_output([
            "nix", "flake", "metadata", "--json", "--no-write-lock-file", "path:" + str(recipe)], text=True))
        snapshot = Path(metadata["path"])
        staging = Path(staging)
        # GC rooty jsou mimo obsah balíčku; platí až do dokončení kopírování.
        subprocess.run(["nix-store", "--add-root", str(staging / "source"),
                        "--indirect", "--realise", str(snapshot)], check=True, stdout=subprocess.DEVNULL)
        root = staging / "bundle"
        root.mkdir()
        frozen_recipe = root / "recipe"
        shutil.copytree(snapshot, frozen_recipe, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", "*.log", ".git", "result", "result-*"))
        # Nix store má adresáře bez práva zápisu; soukromou kopii musíme moci přesunout.
        for directory in [frozen_recipe, *(path for path in frozen_recipe.rglob("*") if path.is_dir())]:
            directory.chmod(directory.stat().st_mode | stat.S_IWUSR)
        flake = "path:" + str(frozen_recipe)
        # Deklarace pochází ze stejného snímku jako obraz, nikoli z dříve sestaveného CLI.
        target = json.loads(subprocess.check_output([
            "nix", "eval", "--json", "--no-write-lock-file",
            flake + "#nixberryDeployment.targets." + target_name], text=True))
        backend = target["backend"]
        selected = method or deployment_method(target)
        attribute = ("nixberryInstallers." if selected == "iso" and backend != "rpi" else "nixberryImages.") + target_name
        extensions = {"rpi": "img.zst", "libvirt": "qcow2", "hyperv": "vhdx",
                      "proxmox": "qcow2", "virtualbox": "vdi", "vmware": "vmdk"}
        if backend not in extensions:
            raise RuntimeError(_('Export obrazu pro tuto platformu není implementovaný.'))
        extension = "iso" if selected == "iso" and backend != "rpi" else extensions[backend]
        result = subprocess.run([
            *command[:-1], "--out-link", str(staging / "image"),
            flake + "#" + attribute], text=True, stdout=subprocess.PIPE, check=True)
        paths = result.stdout.strip().splitlines()
        if len(paths) != 1:
            raise RuntimeError(_('Sestavení nevrátilo právě jeden obraz.'))
        artifacts = list(Path(paths[0]).rglob("*." + extension))
        if len(artifacts) != 1:
            raise RuntimeError(_('Ve výsledku není právě jeden obraz očekávaného formátu.'))
        system = subprocess.check_output(["nix", "eval", "--raw", "--no-write-lock-file",
            flake + "#nixberrySystems." + target_name + ".config.system.build.toplevel.outPath"], text=True).strip()
        filename = "nixberry-" + target_name + "." + extension
        shutil.copyfile(artifacts[0], root / filename)
        manifest = {"target": target_name, "backend": backend, "profile": target["profile"], "deploymentMethod": selected,
                    "configuration": target.get("configuration", "default"),
                    "allowReset": target.get("allowReset", False),
                    "artifact": filename, "format": extension, "spec": target["spec"],
                    "guestHostName": target.get("guestHostName", target["spec"].get("name")),
                    "network": target.get("network", {"mode": "dhcp"}) if backend != "rpi" else None,
                    "systemPath": system,
                    "lockSha256": hashlib.sha256((frozen_recipe / "flake.lock").read_bytes()).hexdigest()}
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        (root / "README.txt").write_text(_(
            "Nixberry: offline balíček\n\n"
            "1. Ověř kontrolní součty: sha256sum -c SHA256SUMS\n"
            "2. Postup přenosu a startu: recipe/README.md\n"
            "3. Cíl a parametry: manifest.json\n\n"
            "Obraz obsahuje systém a jeho závislosti. VM disk startuje přímo;\n"
            "RPi SD je obnovovací prostředí s hotovým cílovým systémem.\n"
            "Instalované služby a aplikace mohou mít vlastní síťové nároky.\n"
        ) + (_("\nPředání pro Proxmox: viz PROXMOX.txt.\n") if backend == "proxmox" else "")
          + (_("\nPředání pro VirtualBox: viz VIRTUALBOX.txt.\n") if backend == "virtualbox" else ""))
        if extension == "iso":
            disko = subprocess.check_output(["nix", "eval", "--raw", "--no-write-lock-file",
                flake + "#nixberrySystems." + target_name + ".config.system.build.diskoScript.outPath"], text=True).strip()
            manifest["diskoPath"] = disko
            (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            (root / "INSTALL-ISO.sh").write_text(shell_script(Path(__file__).with_name("guest-install.sh")))
            disk = "/dev/vda" if backend in ("proxmox", "libvirt") else "/dev/sda"
            (root / "README.txt").write_text(_(
                "Nixberry: offline instalace z ISO\n\n"
                "1. Ověř sha256sum -c SHA256SUMS.\n"
                "2. Vytvoř novou VM s UEFI, vypnutým Secure Boot a právě jedním prázdným diskem.\n"
                "   Parametry CPU, RAM, disk a síť jsou v manifest.json; cílový disk musí být {disk}.\n"
                "   Použij guest agenta a řadič odpovídající platformě popsané v recipe/README.md.\n"
                "3. Připoj {filename} a spusť z něj VM. Systém i disko jsou již součástí ISO.\n"
                "4. Přenes INSTALL-ISO.sh do instalační VM přes SSH nebo další médium.\n"
                "   Nejdřív ověř identitu VM v konzoli hypervizoru a její skutečné DMI UUID:\n"
                "   cat /sys/class/dmi/id/product_uuid\n"
                "5. Kontrola bez zápisu (UUID nahraď ověřenou hodnotou):\n"
                "   bash INSTALL-ISO.sh dry-run UUID {system} {disko} {bytes} {profile} {disk}\n"
                "   Po kontrole zopakuj stejný příkaz s apply místo dry-run.\n"
                "   Instalace odmítá disk s oddíly nebo signaturami; existující systém nepřeinstaluje.\n"
                "6. Korektně vypni VM, odpoj ISO a spusť ji z disku.\n"
                "   Ověř síť a readlink -f /run/current-system proti manifest.json.\n"
                "\nSíť hypervizoru připravuje provozovatel. Síť systému je podle manifest.json.\n"
            ).format(disk=disk, filename=filename, system=system, disko=disko,
                     bytes=target["spec"]["diskGiB"] * 1024**3, profile=target["profile"]))
        if backend == "virtualbox" and extension != "iso":
            network = manifest["network"]
            network_text = (_("Pevná IP: {address}\nBrána: {gateway}\nDNS: {dns}\n").format(
                address=network["address"], gateway=network["gateway"], dns=", ".join(network["dns"]))
                if network.get("mode", "dhcp") == "static" else _("Síť uvnitř VM: DHCP.\n"))
            (root / "VIRTUALBOX.txt").write_text(_(
                "Předání systémového disku pro VirtualBox na Linuxu i Windows\n\n"
                "Obraz: {filename}\nHostname: {hostname}\n{network}"
                "\n1. Ověř SHA256SUMS před importem. Na Linuxu: sha256sum -c SHA256SUMS.\n"
                "   Ve Windows porovnej Get-FileHash -Algorithm SHA256 '{filename}' s řádkem v SHA256SUMS.\n"
                "2. Vytvoř novou VM typu Linux (64-bit), přeskoč bezobslužnou instalaci a nepřipojuj ISO.\n"
                "   Kapacita: {vcpus} CPU, {memory} MiB RAM. Zapni UEFI, Secure Boot ponech vypnutý.\n"
                "3. VDI nejprve zkopíruj mimo balíček; k řadiči SATA připoj tento existující disk.\n"
                "   Obraz má kapacitu {disk} GiB a obsahuje nainstalovaný systém.\n"
                "   Kopii pro další VM vytvoř pomocí VBoxManage clonemedium disk, aby měla vlastní UUID média.\n"
                "4. Připoj jediný síťový adaptér Intel PRO/1000 MT Desktop do zvolené sítě.\n"
                "   Pro DHCP použij Bridged Adapter s DHCP nebo NAT.\n"
                "   Pro pevnou IP musí síť odpovídat uvedené podsíti a bráně.\n"
                "5. Spusť VM z disku a z konzole ověř ip -4 address, ip route a systemctl status virtualbox.\n"
                "   Očekávaný systém (readlink -f /run/current-system) je v manifest.json.\n\n"
                "Při NAT nastav v prostředí provozovatele přesměrování SSH portu.\n"
                "UUID a MAC VM může provozovatel přidělit vlastní; síť není vázaná na MAC.\n"
                "Ruční import nepřebírá značky vlastníka automatické správy hypervizoru.\n"
                "Server spravuj přes access v offline deklaraci nebo --ssh root@HOST:PORT.\n"
                "SSH přijímá klíče z receptu; heslo je vypnuté.\n"
            ).format(filename=filename, hostname=manifest["guestHostName"], network=network_text,
                     vcpus=target["spec"]["vcpus"], memory=target["spec"]["memoryMiB"], disk=target["spec"]["diskGiB"]))
        if backend == "proxmox" and extension != "iso":
            network = manifest["network"]
            network_text = (_("Pevná IP: {address}\nBrána: {gateway}\nDNS: {dns}\n").format(
                address=network["address"], gateway=network["gateway"], dns=", ".join(network["dns"]))
                if network.get("mode", "dhcp") == "static" else _("Síť uvnitř VM: DHCP.\n"))
            (root / "PROXMOX.txt").write_text(_(
                "Předání systémového disku pro VM{vmid}\n\n"
                "Obraz: {filename}\nHostname: {hostname}\n{network}"
                "\n1. Ověř sha256sum -c SHA256SUMS.\n"
                "2. Připrav vypnutou VM s UEFI/OVMF a vypnutým Secure Boot.\n"
                "   Doporučená kapacita: {vcpus} CPU, {memory} MiB RAM, disk {disk} GiB.\n"
                "3. Importuj QCOW2 do zvoleného úložiště a připoj jej jako virtio0 nebo scsi0 (VirtIO SCSI).\n"
                "   Nastav bootování z připojeného disku a povol QEMU Guest Agent.\n"
                "4. Připoj jedinou síťovou kartu VirtIO do dohodnuté sítě a spusť VM.\n"
                "   Pro pevnou IP musí připojená síť odpovídat uvedené podsíti a bráně.\n"
                "5. Z konzole ověř ip -4 address, ip route a resolvectl status.\n"
                "   Systém: readlink -f /run/current-system; očekávaná hodnota je v manifest.json.\n\n"
                "MAC a UUID může provozovatel přidělit vlastní; systémová síť není vázaná na MAC.\n"
                "Ruční import nepřebírá značky vlastnictví požadované správou přes hypervizor.\n"
                "Samotný server lze spravovat přes access v deklaraci nebo nixberry update CIL --ssh root@HOST:PORT --apply.\n"
                "Přístup přes SSH používá klíče z přiloženého receptu, heslo je vypnuté.\n"
                "Obraz dodává systém; konfiguraci hostitele, bridge a firewall spravuje provozovatel.\n"
            ).format(vmid=target["spec"]["vmid"], filename=filename, hostname=manifest["guestHostName"],
                     network=network_text, vcpus=target["spec"]["vcpus"], memory=target["spec"]["memoryMiB"],
                     disk=target["spec"]["diskGiB"]))
        info(_("Počítám kontrolní součty balíčku…"))
        lines = []
        for file in sorted(root.rglob("*")):
            if file.is_file():
                with file.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                lines.append(digest + "  " + str(file.relative_to(root)))
        (root / "SHA256SUMS").write_text("\n".join(lines) + "\n")
        # Obsazený cíl nesmí být přepsán ani po dlouhém sestavení.
        destination.mkdir()
        try:
            for item in root.iterdir():
                shutil.move(str(item), destination / item.name)
        except BaseException:
            # Částečný výsledek ponecháváme k prohlédnutí, nic existujícího nemažeme.
            raise
    return manifest | {"destination": str(destination)}
