{ nixos-raspberrypi, ... }:
{
  imports = [ nixos-raspberrypi.nixosModules.raspberry-pi-5.base ];

  # Firmware loads the kernel directly; this U-Boot cannot read USB SSDs on RPi5.
  # This module supplies matching RPi5 kernel, firmware and device tree.
  boot.loader.raspberry-pi = {
    bootloader = "kernel";
    firmwarePath = "/boot";
    # A separate directory preserves old extlinux files for recovery.
    nixosGenerationsDir = "rpi-generations";
    configurationLimit = 1;
  };
  boot.initrd.availableKernelModules = [
    "uas"
    "sd_mod"
  ];
}
