# Compose physical systems, recovery media and controller tools.
{
  lib,
  vmPkgs,
  configurations,
  unstable,
  emcx,
  nixoncpp,
  disko,
  nixos-raspberrypi,
  rpiTargets,
}:
let
  mkRpi =
    target:
    let
      base = nixos-raspberrypi.lib.nixosSystem {
        system = "aarch64-linux";
        specialArgs.unstable = unstable.legacyPackages.aarch64-linux;
        specialArgs.emcx = emcx;
        specialArgs.nixoncpp = nixoncpp;
        specialArgs.nixberryTargetIdentity = "rpi:${target.hardwareSerial}";
        specialArgs.nixberryModules = {
          common = ../../modules/common.nix;
          emcx = ../../modules/applications/emcx;
          hstr = ../../modules/applications/hstr;
        };
        modules = [
          disko.nixosModules.disko
          ../../platforms/rpi/disk-config.nix
          ../../platforms/rpi/configuration.nix
          ../../platforms/rpi/boot.nix
          configurations.modules.${configurations.select target}
          {
            nixberry.rpi = builtins.removeAttrs target [
              "configuration"
              "backup"
            ];
          }
        ];
      };
    in
    base.extendModules {
      specialArgs.nixberryRpiBootLoader = base.config.system.build.installBootLoader;
      modules = [
        ../../platforms/rpi/security.nix
        ({ config, ... }: {
          assertions = [
            {
              assertion =
                config.nixberry.rpi.rootFilesystem == (target.rootFilesystem or "ext4")
                && config.fileSystems."/".fsType == (target.rootFilesystem or "ext4");
              message = "The selected configuration must preserve the root filesystem declared in the target.";
            }
            {
              assertion =
                config.nixberry.rpi.security == import ./security.nix {
                  security = target.security or { };
                };
              message = "The selected configuration must preserve the RPi security policy declared in the target.";
            }
          ];
        })
      ];
    };
  rpiSystems = builtins.mapAttrs (_: target: mkRpi target) rpiTargets;
  rpiDiskTools = builtins.mapAttrs (
    name: system:
    import ./disk-package.nix {
      pkgs = vmPkgs;
      target = rpiManifests.${name};
      inherit system disko;
    }
  ) rpiSystems;
  rpiRecoveries = builtins.mapAttrs (
    _: target:
    nixos-raspberrypi.lib.nixosSystem {
      system = "aarch64-linux";
      specialArgs.nixberryRpiRecovery = true;
      modules = [
        ./recovery.nix
        {
          nixberry.rpi = builtins.removeAttrs target [
            "configuration"
            "backup"
          ];
        }
      ];
    }
  ) rpiTargets;
  mkRpiManifest =
    name: target: system:
    let
      cfg = system.config.nixberry.rpi;
    in
    {
      target = name;
      configuration = configurations.select target;
      inherit (cfg)
        rootFilesystem
        hostName
        hardwareSerial
        installerAddress
        address
        lanMac
        diskDevice
        diskSerial
        bootLabel
        rootLabel
        ;
      minimumDiskBytes = (cfg.rootGiB * 1024 * 1024 * 1024) + (520 * 1024 * 1024);
      systemPath = toString system.config.system.build.toplevel;
      diskoPath = toString system.config.system.build.diskoScript;
    }
    // lib.optionalAttrs (cfg.network != null) { inherit (cfg) network gateway dns; }
    // lib.optionalAttrs (cfg.security.mode != "none") { inherit (cfg) security; };
  rpiManifests = builtins.mapAttrs (
    name: system: mkRpiManifest name rpiTargets.${name} system
  ) rpiSystems;
  encryptedRpiConfigs = builtins.filter (cfg: cfg.security.mode == "luks") (
    map (system: system.config.nixberry.rpi) (builtins.attrValues rpiSystems)
  );
  recoveryAdapters =
    cfg:
    if cfg.network == null then
      [
        {
          unit = cfg.hardwareSerial;
          mac = cfg.lanMac;
          dhcp = false;
          inherit (cfg) address gateway dns;
        }
      ]
    else
      lib.mapAttrsToList (name: value: {
        unit = "${cfg.hardwareSerial}-${name}";
        inherit (value) mac;
        dhcp = name == cfg.network.recoveryInterface;
        address = "";
        gateway = "";
        dns = [ ];
      }) cfg.network.interfaces;
  encryptedRpiAdapters = lib.concatMap recoveryAdapters encryptedRpiConfigs;
  mkRpiInstaller =
    offline:
    nixos-raspberrypi.nixosConfigurations.rpi5-installer.extendModules {
      modules = [
        ./installer.nix
        ({ lib, ... }: {
          config = lib.mkIf (encryptedRpiConfigs != [ ]) {
            assertions = [
              {
                assertion =
                  builtins.length encryptedRpiAdapters
                  == builtins.length (lib.unique (map (adapter: adapter.mac) encryptedRpiAdapters));
                message = "Encrypted RPi recovery targets must have distinct primary MAC addresses.";
              }
            ];
            networking.useDHCP = lib.mkForce false;
            networking.useNetworkd = lib.mkForce true;
            systemd.network = {
              enable = true;
              networks =
                builtins.listToAttrs (
                  map (adapter: {
                    name = "00-nixberry-${adapter.unit}";
                    value = {
                      matchConfig.MACAddress = adapter.mac;
                      address = lib.optional (adapter.address != "") adapter.address;
                      gateway = lib.optional (adapter.gateway != "") adapter.gateway;
                      dns = adapter.dns;
                      linkConfig = lib.optionalAttrs (adapter.address == "") {
                        RequiredForOnline = if adapter.dhcp then "yes" else "no";
                      };
                      networkConfig = {
                        DHCP = if adapter.dhcp then "ipv4" else "no";
                        IPv6AcceptRA = false;
                      }
                      // lib.optionalAttrs (adapter.address == "") { LinkLocalAddressing = "no"; };
                    };
                  }) encryptedRpiAdapters
                )
                // {
                  "90-recovery-dhcp" = {
                    matchConfig.Name = "en* eth*";
                    networkConfig.DHCP = "yes";
                  };
                };
            };
          };
        })
        {
          environment.etc."nixberry-rpi-targets.json".text =
            if offline then
              builtins.toJSON rpiManifests
            else
              builtins.unsafeDiscardStringContext (builtins.toJSON rpiManifests);
          users.users.root.openssh.authorizedKeys.keyFiles = map (target: target.adminKeyFile) (
            builtins.attrValues rpiTargets
          );
        }
      ];
    };
  rpiInstaller = mkRpiInstaller true;
  rpiNetworkInstaller = mkRpiInstaller false;
  rpiTools = import ./package.nix {
    pkgs = vmPkgs;
    guestJq = rpiInstaller.pkgs.jq;
    targets = rpiManifests;
    recoveries = builtins.mapAttrs (name: system: {
      target = name;
      tree = toString system.config.system.build.kexecTree;
      system = toString system.config.system.build.toplevel;
      params = system.config.boot.kernelParams;
    }) rpiRecoveries;
  };
in
{
  inherit
    mkRpi
    mkRpiManifest
    rpiSystems
    rpiDiskTools
    rpiRecoveries
    rpiManifests
    rpiInstaller
    rpiNetworkInstaller
    rpiTools
    ;
}
