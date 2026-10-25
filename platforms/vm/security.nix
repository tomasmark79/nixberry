# LUKS secrets and the initrd SSH identity are installed only at runtime.
{ config, lib, ... }:
let
  cfg = config.nixberry.vm.security;
  encrypted = cfg.mode != "none";
  network = config.nixberry.vm.network;
  static = network.mode == "static";
  root = config.disko.devices.disk.system.content.partitions.root.content or { };
  luks = config.boot.initrd.luks.devices.nixberry-root or { };
in
{
  options.nixberry.vm.security = lib.mkOption {
    default = { };
    description = "VM disk protection and early-boot unlocking policy.";
    type = lib.types.submodule {
      options = {
        mode = lib.mkOption { type = lib.types.enum [ "none" "luks" "confidential" ]; default = "none"; };
        unlock = lib.mkOption { type = lib.types.enum [ "manual" "attested" ]; default = "manual"; };
        port = lib.mkOption { type = lib.types.port; default = 2222; };
      };
    };
  };
  config = lib.mkIf encrypted {
    assertions = [ {
      assertion = cfg.mode != "confidential";
      message = "Confidential VMs require a supported measured launch and attestation-bound key delivery; this implementation does not provide them. Refusing an unprotected VM.";
    } {
      assertion = config.boot.loader.supportsInitrdSecrets;
      message = "Encrypted VM boot requires a loader that installs initrd SSH secrets outside the Nix store.";
    } {
      assertion = !(builtins.elem cfg.port config.services.openssh.ports);
      message = "Early-boot SSH must use a different port from the installed system SSH service.";
    } {
      assertion = (root.type or "") == "luks" && (root.name or "") == "nixberry-root"
        && (root.passwordFile or null) == "/run/nixberry/luks-password"
        && (root.additionalKeyFiles or [ ]) == [ ];
      message = "Encrypted VM root must use LUKS with the runtime provisioning password and no additional provisioning keys.";
    } {
      assertion = config.swapDevices == [ ];
      message = "Encrypted VM images must not enable swap devices outside the protected root policy.";
    } {
      assertion = config.fileSystems."/".device == "/dev/mapper/nixberry-root"
        && (luks.keyFile or null) == null
        && !(luks.tryEmptyPassphrase or true);
      message = "LUKS VM root must use nixberry-root without an embedded key or empty-passphrase fallback.";
    } ];
    disko.devices.disk.system.content.partitions.root.content = lib.mkForce {
      type = "luks";
      name = "nixberry-root";
      passwordFile = "/run/nixberry/luks-password";
      extraFormatArgs = [ "--type" "luks2" ];
      content = {
        type = "filesystem";
        format = "ext4";
        mountpoint = "/";
        mountOptions = [ "noatime" ];
      };
    };
    disko.imageBuilder.extraRootModules = [ "dm_crypt" "xts" "aesni_intel" ];
    boot.initrd = {
      systemd.enable = true;
      availableKernelModules = [ "virtio_net" "e1000" "e1000e" "vmxnet3" "hv_netvsc" ];
      network = {
        enable = true;
        ssh = {
          enable = true;
          inherit (cfg) port;
          hostKeys = [ "/etc/secrets/initrd/ssh_host_ed25519_key" ];
          authorizedKeyFiles = lib.mkForce [ ];
          authorizedKeys = lib.mkForce (map
            (key: ''restrict,pty,command="systemctl default" ${key}'')
            (builtins.filter (line: line != "" && !(lib.hasPrefix "#" line))
              (lib.splitString "\n" (lib.fileContents ../../admin.pub))));
        };
      };
      # systemctl starts the default target and handles repeated password requests.
      # Forced SSH commands prevent a general-purpose early-boot root shell.
      systemd.users.root.shell = "/bin/sh";
      systemd.network = {
        enable = true;
        networks."10-lan" = {
          matchConfig = if config.nixberry.vm.adapters == [ ] then { Name = "en* eth*"; }
            else { MACAddress = (builtins.head config.nixberry.vm.adapters).mac; };
          networkConfig = {
            DHCP = if static then "no" else "ipv4";
            IPv6AcceptRA = false;
          } // lib.optionalAttrs static {
            Address = network.address;
            Gateway = network.gateway;
            DNS = network.dns;
          };
        };
      };
    };
    # No hibernation image or swap outside the encrypted root.
    swapDevices = lib.mkForce [ ];
  };
}
