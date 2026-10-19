{ nixos-raspberrypi, ... }:
{
  imports = [ nixos-raspberrypi.nixosModules.raspberry-pi-5.base ];

  # Firmware načte kernel přímo. Použitý U-Boot neumí na RPi5 číst USB SSD.
  # Modul dodává vzájemně odpovídající kernel, firmware a device tree pro RPi5.
  boot.loader.raspberry-pi = {
    bootloader = "kernel";
    firmwarePath = "/boot";
    # Oddělený adresář zachová staré soubory extlinux pro případ návratu.
    nixosGenerationsDir = "rpi-generations";
    configurationLimit = 1;
  };
  boot.initrd.availableKernelModules = [
    "uas"
    "sd_mod"
  ];
}
