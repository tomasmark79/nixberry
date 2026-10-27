{
  description = "NixBerry — declarative NixOS server deployment and management for VMs and Raspberry Pi 5";

  inputs = {
    # nixos-anywhere uses the same revision as the installation SD.
    nixpkgs.url = "github:NixOS/nixpkgs/7a0f122f5090cf4c2ade2a13a0e229d4e19ba71f";
    # Selected applications may use unstable independently of the system base.
    unstable.url = "github:NixOS/nixpkgs/nixos-unstable";
    # Upstream package; dependencies share the pinned NixBerry base.
    nixoncpp = {
      url = "github:tomasmark79/NixonCpp?dir=nix";
      inputs.nixpkgs.follows = "nixos-raspberrypi/nixpkgs";
      inputs.nixpkgs-wasm.follows = "nixos-raspberrypi/nixpkgs";
    };
    emcx = {
      url = "github:tomasmark79/emcx";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.mc-upstream.follows = "mc-upstream";
    };
    mc-upstream = {
      url = "github:MidnightCommander/mc/master";
      flake = false;
    };
    # Updates follow main; flake.lock pins the exact revision in use.
    nixos-raspberrypi.url = "github:nvmd/nixos-raspberrypi/main";
    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nixos-anywhere = {
      url = "github:nix-community/nixos-anywhere/main";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.disko.follows = "disko";
    };
  };

  outputs =
    {
      nixpkgs,
      unstable,
      nixoncpp,
      emcx,
      disko,
      nixos-anywhere,
      nixos-raspberrypi,
      ...
    }:
    let
      lib = nixos-raspberrypi.inputs.nixpkgs.lib;
      vmPkgs = nixos-raspberrypi.inputs.nixpkgs.legacyPackages.x86_64-linux;
      deployment = import ./management/deployment;
      configurations = import ./management/deployment/configurations.nix;
      rpiTargets = import ./management/rpi/targets;
      mkRpi =
        target:
        let
          base = nixos-raspberrypi.lib.nixosSystem {
            system = "aarch64-linux";
            specialArgs.unstable = unstable.legacyPackages.aarch64-linux;
            specialArgs.emcx = emcx;
            specialArgs.nixoncpp = nixoncpp;
            specialArgs.nixberryModules.common = ./modules/common.nix;
            modules = [
              disko.nixosModules.disko
              ./platforms/rpi/disk-config.nix
              ./platforms/rpi/configuration.nix
              ./platforms/rpi/boot.nix
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
            ./platforms/rpi/security.nix
            ({ config, ... }: {
              assertions = [
                {
                  assertion =
                    config.nixberry.rpi.security == import ./management/rpi/security.nix {
                      security = target.security or { };
                    };
                  message = "The selected configuration must preserve the RPi security policy declared in the target.";
                }
              ];
            })
          ];
        };
      rpiSystems = builtins.mapAttrs (_: target: mkRpi target) rpiTargets;
      rpiRecoveries = builtins.mapAttrs (
        _: target:
        nixos-raspberrypi.lib.nixosSystem {
          system = "aarch64-linux";
          specialArgs.nixberryRpiRecovery = true;
          modules = [
            ./management/rpi/recovery.nix
            {
              nixberry.rpi = builtins.removeAttrs target [
                "configuration"
                "backup"
              ];
            }
          ];
        }
      ) rpiTargets;
      rpiManifests = builtins.mapAttrs (
        name: system:
        let
          cfg = system.config.nixberry.rpi;
        in
        {
          target = name;
          configuration = configurations.select rpiTargets.${name};
          inherit (cfg)
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
        // lib.optionalAttrs (cfg.security.mode != "none") { inherit (cfg) security; }
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
            ./management/rpi/installer.nix
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
      rpiTools = import ./management/rpi/package.nix {
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
      mkVm =
        profile:
        lib.nixosSystem {
          system = "x86_64-linux";
          specialArgs.unstable = unstable.legacyPackages.x86_64-linux;
          specialArgs.emcx = emcx;
          specialArgs.nixoncpp = nixoncpp;
          specialArgs.nixberryModules.common = ./modules/common.nix;
          modules = [
            disko.nixosModules.disko
            ./platforms/vm/disk-config.nix
            ./platforms/vm/configuration.nix
            ./platforms/vm/security.nix
            profile
          ];
        };
      profiles = {
        proxmox = {
          profile = "nixberry-proxmox";
          system = mkVm ./platforms/vm/profiles/qemu.nix;
          disk = "/dev/vda";
          modules = [ ];
        };
        virtualbox = {
          profile = "nixberry-virtualbox";
          system = mkVm ./platforms/vm/profiles/virtualbox.nix;
          disk = "/dev/sda";
          modules = [
            ({ lib, ... }: {
              services.qemuGuest.enable = lib.mkForce false;
              virtualisation.virtualbox.guest.enable = true;
            })
          ];
        };
        vmware = {
          profile = "nixberry-vmware";
          system = mkVm ./platforms/vm/profiles/vmware.nix;
          disk = "/dev/sda";
          modules = [
            ({ lib, ... }: {
              services.qemuGuest.enable = lib.mkForce false;
              virtualisation.vmware.guest.enable = true;
            })
          ];
        };
        libvirt = {
          profile = "nixberry-vm";
          system = mkVm ./platforms/vm/profiles/qemu.nix;
          disk = "/dev/vda";
          modules = [ ];
        };
        hyperv = {
          profile = "nixberry-hyperv";
          system = mkVm ./platforms/vm/profiles/hyperv.nix;
          disk = "/dev/sda";
          modules = [
            ({ lib, ... }: {
              services.qemuGuest.enable = lib.mkForce false;
              virtualisation.hypervGuest.enable = true;
              boot.initrd.availableKernelModules = [
                "hv_vmbus"
                "hv_storvsc"
              ];
            })
          ];
        };
      };
      mkVmInstaller =
        profile: targetSystem: disk: extraModules:
        lib.nixosSystem {
          system = "x86_64-linux";
          modules = [
            ./management/vm/installer.nix
            {
              isoImage.storeContents = [
                targetSystem.config.system.build.toplevel
                targetSystem.config.system.build.diskoScript
              ];
              environment.etc."nixberry-installation.json".text = builtins.toJSON {
                inherit profile disk;
                system = targetSystem.config.system.build.toplevel;
                disko = targetSystem.config.system.build.diskoScript;
              };
            }
          ]
          ++ extraModules;
        };
      # Profiles are independent of concrete VM inventory.
      defaultVmSystems = builtins.mapAttrs (
        _: profile:
        profile.system.extendModules {
          modules = [ configurations.modules.default ];
        }
      ) profiles;
      genericInstallers = builtins.mapAttrs (
        name: profile: mkVmInstaller profile.profile defaultVmSystems.${name} profile.disk profile.modules
      ) profiles;
      vmSystems = builtins.mapAttrs (
        _: target:
        profiles.${target.backend}.system.extendModules {
          modules = [
            configurations.modules.${target.configuration}
            ({ config, lib, ... }: {
              networking.hostName = lib.mkForce target.guestHostName;
              nixberry.vm.network = target.network or { };
              nixberry.vm.adapters = guestAdapters target;
              nixberry.vm.security = builtins.removeAttrs target.security [ "imageDirectory" ];
              assertions = [
                {
                  assertion = config.nixberry.vm.adapters == guestAdapters target;
                  message = "The selected configuration must preserve the VM adapter identities declared in the instance.";
                }
                {
                  assertion =
                    config.nixberry.vm.security == builtins.removeAttrs target.security [ "imageDirectory" ];
                  message = "The selected configuration must preserve the VM security policy declared in the instance.";
                }
              ];
            })
          ];
        }
      ) deployment.instances;
      vmInstallers = builtins.mapAttrs (
        name: target:
        let
          profile = profiles.${target.backend};
        in
        (mkVmInstaller target.profile vmSystems.${name} profile.disk profile.modules).extendModules {
          modules = [
            {
              nixberry.vm.network = target.network or { };
              nixberry.vm.adapters = guestAdapters target;
            }
          ];
        }
      ) deployment.instances;
      guestAdapters =
        target:
        map (adapter: {
          inherit (adapter) name;
          mac =
            let
              compact = lib.toLower (builtins.replaceStrings [ ":" "-" ] [ "" "" ] adapter.mac);
            in
            lib.concatStringsSep ":" (builtins.genList (i: builtins.substring (i * 2) 2 compact) 6);
        }) (target.spec.networkAdapters or [ ]);
      mkVmTools =
        method:
        builtins.mapAttrs (
          name: target:
          import (./management + "/${target.backend}/package.nix") {
            pkgs = vmPkgs;
            spec = target.spec;
            inherit (target) guestHostName allowReset;
            installerIso = vmInstallers.${name}.config.system.build.isoImage;
            isoName = vmInstallers.${name}.config.image.fileName;
            targetSystem = vmSystems.${name}.config.system.build.toplevel;
            diskoScript = vmSystems.${name}.config.system.build.diskoScript;
            image =
              if target.security.mode == "none" then
                vmImages.${name}
              else if target.security.imageDirectory != null then
                target.security.imageDirectory
              else
                "/run/nixberry/unprepared-image";
            security = target.security;
            securityTarget = target;
            deploymentMethod = if method == null then target.deploymentMethod else method;
          }
        ) (lib.filterAttrs (_: target: !target.offline) deployment.instances);
      vmTools = mkVmTools null;
      mkVmImage =
        system: size:
        (system.extendModules {
          modules = [
            ./management/vm/image.nix
            { disko.devices.disk.system.imageSize = "${toString size}G"; }
          ];
        }).config.system.build.diskoImages;
      mkHypervImage =
        system: size:
        vmPkgs.runCommand "nixberry-hyperv-image" { nativeBuildInputs = [ vmPkgs.qemu ]; } ''
          mkdir -p "$out"
          qemu-img convert -f qcow2 -O vhdx -o subformat=dynamic \
            ${mkVmImage system size}/system.qcow2 "$out/system.vhdx"
        '';
      mkPortableImage =
        backend: system: size:
        let
          format =
            {
              virtualbox = "vdi";
              vmware = "vmdk";
            }
            .${backend};
          options = lib.optionalString (backend == "vmware") "-o subformat=monolithicSparse";
        in
        vmPkgs.runCommand "nixberry-${backend}-image" { nativeBuildInputs = [ vmPkgs.qemu ]; } ''
          mkdir -p "$out"
          qemu-img convert -f qcow2 -O ${format} ${options} \
            ${mkVmImage system size}/system.qcow2 "$out/system.${format}"
          qemu-img info --output=json "$out/system.${format}" > "$out/image-info.json"
        '';
      vmImages = builtins.mapAttrs (
        name: target:
        if target.security.mode != "none" then
          throw "Encrypted images must be prepared outside the Nix store with nixberry prepare-image TARGET --output DIR."
        else if target.backend == "hyperv" then
          mkHypervImage vmSystems.${name} target.spec.diskGiB
        else if
          builtins.elem target.backend [
            "virtualbox"
            "vmware"
          ]
        then
          mkPortableImage target.backend vmSystems.${name} target.spec.diskGiB
        else
          mkVmImage vmSystems.${name} target.spec.diskGiB
      ) deployment.instances;
      images = vmImages // builtins.mapAttrs (_: _: rpiInstaller.config.system.build.sdImage) rpiTargets;
      deploymentSource = import ./management/deployment/source-package.nix { pkgs = vmPkgs; };
      deploymentTools = import ./management/deployment/package.nix {
        pkgs = vmPkgs;
      };
      baseSystems = {
        nixberry-vm = defaultVmSystems.libvirt;
        nixberry-vmware = defaultVmSystems.vmware;
        nixberry-virtualbox = defaultVmSystems.virtualbox;
        nixberry-proxmox = defaultVmSystems.proxmox;
        nixberry-hyperv = defaultVmSystems.hyperv;
        nixberry-installer = genericInstallers.libvirt;
        nixberry-hyperv-installer = genericInstallers.hyperv;
        nixberry-rpi-installer = rpiInstaller;
      };
      # Direct libvirt outputs remain available; every backend has nixberrySystems.TARGET.
      namedVmSystems = lib.mapAttrs' (name: system: lib.nameValuePair "nixberry-libvirt-${name}" system) (
        lib.filterAttrs (name: _: deployment.instances.${name}.backend == "libvirt") vmSystems
      );
      namedRecoveries = lib.mapAttrs' (
        name: system: lib.nameValuePair "nixberry-rpi-recovery-${name}" system
      ) rpiRecoveries;
      allSystems = baseSystems // rpiSystems // namedVmSystems // namedRecoveries;
    in
    assert
      builtins.length (builtins.attrNames allSystems) == builtins.length (builtins.attrNames baseSystems)
      + builtins.length (builtins.attrNames rpiSystems)
      + builtins.length (builtins.attrNames namedVmSystems)
      + builtins.length (builtins.attrNames namedRecoveries)
      || throw "The target name conflicts with a system profile name.";
    {
      nixberryDeployment = deployment;
      # Unified verify/export outputs never select a first or fixed instance.
      nixberrySystems = rpiSystems // vmSystems;
      nixberryConfigurations = builtins.attrNames configurations.modules;
      nixberryImages = images;
      nixberryImageBuilders = builtins.mapAttrs (
        name: target:
        (vmSystems.${name}.extendModules {
          modules = [
            ./management/vm/image.nix
            { disko.devices.disk.system.imageSize = "${toString target.spec.diskGiB}G"; }
          ];
        }).config.system.build.diskoImagesScript
      ) deployment.instances;
      nixberryTools = vmTools;
      nixberryToolsByMethod = {
        image = mkVmTools "image";
        iso = mkVmTools "iso";
      };
      nixberryInstallers = builtins.mapAttrs (
        _: installer: installer.config.system.build.isoImage
      ) vmInstallers;
      nixberryRpiRecoveries = rpiRecoveries;
      nixosConfigurations = allSystems;
      devShells.x86_64-linux.ci = vmPkgs.mkShellNoCC {
        packages = with vmPkgs; [
          actionlint
          bash
          gettext
          git
          jq
          libxml2.bin
          nvd
          powershell
          python3
          ruff
          shellcheck
        ];
      };
      packages.x86_64-linux = {
        inherit (emcx.packages.x86_64-linux) emcx emcx-tests;
        hstr = vmPkgs.callPackage ./modules/applications/hstr/package.nix { };
        nixberry = deploymentTools;
        nixberry-source = deploymentSource;
        nixberry-qemu-image = mkVmImage defaultVmSystems.libvirt 32;
        nixberry-hyperv-image = mkHypervImage defaultVmSystems.hyperv 32;
        nixberry-proxmox-image = mkVmImage defaultVmSystems.proxmox 32;
        nixberry-virtualbox-image = mkPortableImage "virtualbox" defaultVmSystems.virtualbox 32;
        nixberry-vmware-image = mkPortableImage "vmware" defaultVmSystems.vmware 32;
        nixberry-images = vmPkgs.linkFarm "nixberry-images" (
          lib.mapAttrsToList (name: path: { inherit name path; }) images
        );
        nixberry-installer = genericInstallers.libvirt.config.system.build.isoImage;
        nixberry-hyperv-installer = genericInstallers.hyperv.config.system.build.isoImage;
        nixberry-proxmox-installer = genericInstallers.proxmox.config.system.build.isoImage;
        nixberry-virtualbox-installer = genericInstallers.virtualbox.config.system.build.isoImage;
        nixberry-vmware-installer = genericInstallers.vmware.config.system.build.isoImage;
        nixberry-rpi-installer = rpiInstaller.config.system.build.sdImage;
        nixberry-rpi-network-installer = rpiNetworkInstaller.config.system.build.sdImage;
        nixberry-rpi-tools = rpiTools;
        nixberry-rpi-recoveries = vmPkgs.linkFarm "nixberry-rpi-recoveries" (
          lib.mapAttrsToList (name: system: {
            inherit name;
            path = system.config.system.build.kexecTree;
          }) rpiRecoveries
        );
        nixos-anywhere = nixos-anywhere.packages.x86_64-linux.default;
      };
      packages.aarch64-linux.hstr =
        nixos-raspberrypi.inputs.nixpkgs.legacyPackages.aarch64-linux.callPackage
          ./modules/applications/hstr/package.nix
          { };
      packages.aarch64-linux.emcx = emcx.packages.aarch64-linux.emcx;
      packages.aarch64-linux.emcx-tests = emcx.packages.aarch64-linux.emcx-tests;
      packages.aarch64-linux.nixberry-rpi-local-tools = builtins.head (
        builtins.filter (
          package: (package.name or "") == "nixberry-install-local"
        ) rpiInstaller.config.environment.systemPackages
      );
      apps.x86_64-linux = {
        nixberry-source = {
          type = "app";
          program = "${deploymentSource}/bin/nixberry-source";
        };
        nixberry = {
          type = "app";
          program = "${deploymentTools}/bin/nixberry";
        };
        nixberry-rpi-install = {
          type = "app";
          program = "${rpiTools}/bin/nixberry-rpi-install";
        };
        nixberry-rpi-recovery-test = {
          type = "app";
          program = "${rpiTools}/bin/nixberry-rpi-recovery-test";
        };
      };
    };
}
