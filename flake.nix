{
  description = "nixberry — deklarativní NixOS server, reprodukce a správa RPi5 a virtuálních strojů";

  inputs = {
    # Nástroj nixos-anywhere používá stejnou revizi jako instalační SD karta.
    nixpkgs.url = "github:NixOS/nixpkgs/7a0f122f5090cf4c2ade2a13a0e229d4e19ba71f";
    # Vybrané aplikace mohou používat unstable nezávisle na základu systému.
    unstable.url = "github:NixOS/nixpkgs/nixos-unstable";
    # Hotový balíček z projektu; závislosti sdílejí připnutý základ NixBerry.
    nixoncpp = {
      url = "github:tomasmark79/NixonCpp?dir=nix";
      inputs.nixpkgs.follows = "nixos-raspberrypi/nixpkgs";
      inputs.nixpkgs-wasm.follows = "nixos-raspberrypi/nixpkgs";
    };
    mc-modern = {
      # Větev forku sleduje nové změny; konkrétní revizi drží zdejší flake.lock.
      url = "github:tomasmark79/mc/mc-modern";
      inputs.nixpkgs.follows = "nixos-raspberrypi/nixpkgs";
    };
    # Aktualizace sledují main; konkrétní použitou revizi drží flake.lock.
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
      mc-modern,
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
        nixos-raspberrypi.lib.nixosSystem {
          system = "aarch64-linux";
          specialArgs.unstable = unstable.legacyPackages.aarch64-linux;
          specialArgs.mc-modern = mc-modern;
          specialArgs.nixoncpp = nixoncpp;
          modules = [
            disko.nixosModules.disko
            ./platforms/rpi/disk-config.nix
            ./platforms/rpi/configuration.nix
            ./platforms/rpi/boot.nix
            configurations.modules.${configurations.select target}
            { nixberry.rpi = builtins.removeAttrs target [ "configuration" ]; }
          ];
        };
      rpiSystems = builtins.mapAttrs (_: target: mkRpi target) rpiTargets;
      rpiRecoveries = builtins.mapAttrs (
        _: target:
        nixos-raspberrypi.lib.nixosSystem {
          system = "aarch64-linux";
          modules = [
            ./management/rpi/recovery.nix
            { nixberry.rpi = builtins.removeAttrs target [ "configuration" ]; }
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
      ) rpiSystems;
      mkRpiInstaller =
        offline:
        nixos-raspberrypi.nixosConfigurations.rpi5-installer.extendModules {
          modules = [
            ./management/rpi/installer.nix
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
          specialArgs.mc-modern = mc-modern;
          specialArgs.nixoncpp = nixoncpp;
          modules = [
            disko.nixosModules.disko
            ./platforms/vm/disk-config.nix
            ./platforms/vm/configuration.nix
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
      # Profily jsou nezávislé na evidenci konkrétních VM.
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
            ({ lib, ... }: {
              networking.hostName = lib.mkForce target.guestHostName;
              nixberry.vm.network = target.network or { };
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
          modules = [ { nixberry.vm.network = target.network or { }; } ];
        }
      ) deployment.instances;
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
            image = vmImages.${name};
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
        if target.backend == "hyperv" then
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
      # Původní libvirt výstupy zůstávají dostupné; všechny backendy mají nixberrySystems.CÍL.
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
      || throw "Název cíle koliduje s názvem systémového profilu.";
    {
      nixberryDeployment = deployment;
      # Jednotné výstupy pro verify/export nikdy nevybírají první ani pevnou instanci.
      nixberrySystems = rpiSystems // vmSystems;
      nixberryConfigurations = builtins.attrNames configurations.modules;
      nixberryImages = images;
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
      packages.x86_64-linux = {
        inherit (mc-modern.packages.x86_64-linux) mc mc-tests;
        hstr = vmPkgs.callPackage ./modules/applications/hstr/package.nix { };
        nixberry = deploymentTools;
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
      packages.aarch64-linux.mc = mc-modern.packages.aarch64-linux.mc;
      packages.aarch64-linux.mc-tests = mc-modern.packages.aarch64-linux.mc-tests;
      packages.aarch64-linux.nixberry-rpi-local-tools = builtins.head (
        builtins.filter (
          package: (package.name or "") == "nixberry-install-local"
        ) rpiInstaller.config.environment.systemPackages
      );
      apps.x86_64-linux = {
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
