{
  config,
  lib,
  pkgs,
  ...
}:
let
  # Stejné základní nastavení jako v sd-image-aarch64.nix této revize nixpkgs.
  firmwareConfig = pkgs.writeText "nixberry-config.txt" ''
    [all]
    arm_64bit=1
    kernel=u-boot.bin
    avoid_warnings=1
    [pi5]
    enable_uart=0
  '';
in
{
  boot.loader.grub.enable = false;
  boot.loader.generic-extlinux-compatible = {
    enable = true;
    configurationLimit = 3;
  };
  boot.loader.timeout = 5;
  boot.kernelPackages = pkgs.linuxPackages;
  boot.kernelParams = [ "console=tty0" ];
  boot.initrd.availableKernelModules = [
    "xhci_pci"
    "xhci_hcd"
    "usb_storage"
    "uas"
    "sd_mod"
    "pcie-brcmstb"
    "rp1_pci"
    "pinctrl-rp1"
    "clk-rp1"
  ];
  hardware.enableRedistributableFirmware = true;
  hardware.deviceTree.enable = true;
  hardware.deviceTree.filter = "bcm2712-rpi-5-b.dtb";
  hardware.deviceTree.name = "broadcom/bcm2712-rpi-5-b.dtb";

  # Extlinux samo firmware ani U-Boot neinstaluje. Tento skript doplní obojí
  # při instalaci i při pozdější aktualizaci startovacích souborů.
  system.build.installBootLoader = lib.mkForce (
    pkgs.writeShellScript "install-nixberry-boot" ''
      set -euo pipefail
      ${pkgs.util-linux}/bin/mountpoint -q /boot || {
        echo "Chyba: startovací oddíl /boot není připojený." >&2
        exit 1
      }
      ${config.boot.loader.generic-extlinux-compatible.populateCmd} -d /boot -c "$1"
      ${pkgs.coreutils}/bin/install -m 0644 ${pkgs.ubootRaspberryPiAarch64}/u-boot.bin /boot/u-boot.bin
      ${pkgs.coreutils}/bin/install -m 0644 ${pkgs.raspberrypifw}/share/raspberrypi/boot/bcm2712-rpi-5-b.dtb /boot/
      ${pkgs.coreutils}/bin/install -m 0644 ${pkgs.raspberrypifw}/share/raspberrypi/boot/bcm2712-d-rpi-5-b.dtb /boot/
      ${pkgs.coreutils}/bin/install -m 0644 ${firmwareConfig} /boot/config.txt
    ''
  );
}
