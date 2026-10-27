# Runtime secrets are appended to a copied initrd before generation promotion.
{
  config,
  lib,
  pkgs,
  nixberryRpiBootLoader,
  ...
}:
let
  cfg = config.nixberry.rpi;
  security = cfg.security;
  named = cfg.network != null;
  selectedName =
    if (security.interface or null) != null then
      security.interface
    else if named then
      cfg.network.primary
    else
      "10-lan";
  selected =
    if named then
      cfg.network.interfaces.${selectedName}
    else
      {
        mac = cfg.lanMac;
        address = cfg.address;
        gateway = cfg.gateway;
        dns = cfg.dns;
      };
  earlyNetworks = {
    "10-lan" = {
      matchConfig.MACAddress = selected.mac;
      address = [ (if (security.address or null) == null then selected.address else security.address) ];
      gateway = lib.optional (selected.gateway != "") selected.gateway;
      dns = selected.dns;
      networkConfig = {
        DHCP = "no";
        IPv6AcceptRA = false;
      };
    };
  };
  original = lib.splitString " " nixberryRpiBootLoader;
  loader =
    pkgs.runCommand "nixberry-rpi-secret-loader"
      {
        nativeBuildInputs = [ pkgs.buildPackages.python3 ];
      }
      ''
        python3 ${../../management/rpi/boot-secrets.py} ${lib.escapeShellArg (builtins.head original)} "$out"
      '';
  root = config.disko.devices.disk.ssd.content.partitions.root.content or { };
  luks = config.boot.initrd.luks.devices.nixberry-root or { };
in
{
  config = lib.mkIf (security.mode == "luks") {
    assertions = [
      {
        assertion = config.boot.loader.raspberry-pi.bootloader == "kernel";
        message = "RPi LUKS requires the generational kernel bootloader.";
      }
      {
        assertion =
          config.boot.loader.supportsInitrdSecrets
          && config.boot.initrd.network.ssh.hostKeys == [ "/etc/secrets/initrd/ssh_host_ed25519_key" ];
        message = "RPi early-boot SSH secrets must be installed at runtime, outside the Nix store.";
      }
      {
        assertion =
          builtins.attrNames config.disko.devices.disk == [ "ssd" ]
          && config.disko.devices.disk.ssd.device == cfg.diskDevice
          && config.disko.devices.disk.ssd.content.partitions.root.label == cfg.rootLabel
          && config.disko.devices.disk.ssd.content.partitions.boot.label == cfg.bootLabel;
        message = "RPi encryption must preserve the declared physical disk and partition identities.";
      }
      {
        assertion =
          (root.type or "") == "luks"
          && (root.name or "") == "nixberry-root"
          && (root.passwordFile or null) == "/run/nixberry/luks-password"
          && (root.additionalKeyFiles or [ ]) == [ ];
        message = "RPi encrypted root must use the runtime provisioning password without additional keys.";
      }
      {
        assertion =
          config.fileSystems."/".device == "/dev/mapper/nixberry-root"
          && (luks.keyFile or null) == null
          && !(luks.tryEmptyPassphrase or true);
        message = "RPi LUKS root must not embed a volume key or enable empty-passphrase fallback.";
      }
      {
        assertion = !(builtins.elem security.port config.services.openssh.ports);
        message = "RPi early-boot SSH must use a different port from ordinary SSH.";
      }
      {
        assertion = builtins.all (swap: swap.device == "/swapfile") config.swapDevices;
        message = "RPi encrypted systems permit only the swap file inside the protected root.";
      }
    ];
    boot.loader.supportsInitrdSecrets = true;
    system.build.installBootLoader = lib.mkForce (
      "${loader}/${builtins.baseNameOf (builtins.head original)} "
      + lib.concatStringsSep " " (builtins.tail original)
    );
    disko.devices.disk.ssd.content.partitions.root.content = lib.mkForce {
      type = "luks";
      name = "nixberry-root";
      passwordFile = "/run/nixberry/luks-password";
      extraFormatArgs = [
        "--type"
        "luks2"
      ];
      content = {
        type = "filesystem";
        format = "ext4";
        extraArgs = [
          "-L"
          cfg.rootLabel
        ];
        mountpoint = "/";
        mountOptions = [ "noatime" ];
      };
    };
    boot.initrd = {
      systemd.enable = true;
      availableKernelModules = [
        "rp1_pci"
        "aes_ce_blk"
      ]
      ++ lib.optionals named [
        "xhci_pci"
        "xhci_hcd"
        "usbnet"
        "r8152"
        "asix"
        "ax88179_178a"
        "cdc_ether"
        "cdc_ncm"
      ];
      network = {
        enable = true;
        ssh = {
          enable = true;
          port = security.port;
          hostKeys = [ "/etc/secrets/initrd/ssh_host_ed25519_key" ];
          authorizedKeyFiles = lib.mkForce [ ];
          authorizedKeys = lib.mkForce (
            map (key: ''restrict,pty,command="systemctl default" ${key}'') (
              builtins.filter (line: line != "" && !(lib.hasPrefix "#" line)) (
                lib.splitString "\n" (lib.fileContents cfg.adminKeyFile)
              )
            )
          );
        };
      };
      systemd.users.root.shell = "/bin/sh";
      systemd.network = {
        enable = true;
        links = lib.mkIf named (
          lib.mkForce {
            "10-nixberry-unlock" = {
              matchConfig.MACAddress = selected.mac;
              linkConfig.Name = selectedName;
            };
          }
        );
        networks = if named then lib.mkForce earlyNetworks else earlyNetworks;
      };
    };
  };
}
