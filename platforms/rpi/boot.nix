{
  config,
  lib,
  nixos-raspberrypi,
  ...
}:
{
  imports = [
    nixos-raspberrypi.nixosModules.raspberry-pi-5.base
    nixos-raspberrypi.nixosModules.raspberry-pi-5.display-vc4
    nixos-raspberrypi.nixosModules.raspberry-pi-5.bluetooth
  ];
  # Add Xorg HDMI selection and Bluetooth for graphical configurations.
  # The upstream base already supplies the VC4 KMS firmware overlay.
  hardware.raspberry-pi = {
    display-vc4.enable = lib.mkDefault config.services.xserver.enable;
    bluetooth.enable = lib.mkDefault config.services.xserver.enable;
  };

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
