# Installation environment for headless x86_64 test VMs.
{ lib, modulesPath, ... }:
{
  imports = [
    (modulesPath + "/installer/cd-dvd/installation-cd-minimal.nix")
    ../../modules/services/ssh.nix
    ../../platforms/vm/system/networking.nix
  ];

  networking.hostName = "nixberry-installer";
  # The ISO uses the same networkd setup as the target VM.
  networking.networkmanager.enable = lib.mkForce false;
  networking.wireless.enable = lib.mkForce false;
  users.users.root.openssh.authorizedKeys.keyFiles = [ ../../admin.pub ];
  services.qemuGuest.enable = true;
  boot.kernelParams = [
    "console=tty0"
    "console=ttyS0,115200n8"
  ];
  isoImage.squashfsCompression = "zstd -Xcompression-level 3";
  boot.zfs.forceImportRoot = false;
  system.stateVersion = "26.05";
}
