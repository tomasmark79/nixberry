# Obnovovací prostředí v RAM pro ověření kexec. SSD se nepřipojuje ani nemění.
{
  config,
  lib,
  modulesPath,
  nixos-raspberrypi,
  ...
}:
{
  imports = [
    nixos-raspberrypi.nixosModules.raspberry-pi-5.base
    (modulesPath + "/installer/netboot/netboot-minimal.nix")
    ../../platforms/rpi/options.nix
    ../../platforms/rpi/system/networking/networkd.nix
    ../../modules/services/ssh.nix
  ];
  networking.hostName = lib.mkForce "nixberry-recovery";
  boot.loader.raspberry-pi.enable = lib.mkForce false;
  hardware.enableAllHardware = lib.mkForce false;
  hardware.enableRedistributableFirmware = lib.mkForce false;
  boot.initrd.availableKernelModules = [
    "uas"
    "sd_mod"
  ];
  users.users.root.openssh.authorizedKeys.keyFiles = [ config.nixberry.rpi.adminKeyFile ];
  environment.etc."nixberry-recovery-kind".text = "ram-kexec\n";
  boot.zfs.forceImportRoot = false;
  time.timeZone = "Europe/Prague";
  system.stateVersion = "26.05";
}
