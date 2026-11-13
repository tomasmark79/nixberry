# Compose VM systems, installation media and backend tools.
{
  lib,
  vmPkgs,
  configurations,
  unstable,
  emcx,
  nixoncpp,
  disko,
  deployment,
}:
let
  mkVm =
    profile:
    lib.nixosSystem {
      system = "x86_64-linux";
      specialArgs.unstable = unstable.legacyPackages.x86_64-linux;
      specialArgs.emcx = emcx;
      specialArgs.nixoncpp = nixoncpp;
      specialArgs.nixberryModules = {
        common = ../../modules/common.nix;
        emcx = ../../modules/applications/emcx;
        hstr = ../../modules/applications/hstr;
      };
      modules = [
        disko.nixosModules.disko
        ../../platforms/vm/disk-config.nix
        ../../platforms/vm/configuration.nix
        ../../platforms/vm/security.nix
        profile
      ];
    };
  profiles = {
    proxmox = {
      profile = "nixberry-proxmox";
      system = mkVm ../../platforms/vm/profiles/qemu.nix;
      disk = "/dev/vda";
      modules = [ ];
    };
    virtualbox = {
      profile = "nixberry-virtualbox";
      system = mkVm ../../platforms/vm/profiles/virtualbox.nix;
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
      system = mkVm ../../platforms/vm/profiles/vmware.nix;
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
      system = mkVm ../../platforms/vm/profiles/qemu.nix;
      disk = "/dev/vda";
      modules = [ ];
    };
    hyperv = {
      profile = "nixberry-hyperv";
      system = mkVm ../../platforms/vm/profiles/hyperv.nix;
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
        ./installer.nix
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
      specialArgs.nixberryTargetIdentity = "${target.backend}:${
        target.spec.uuid or target.spec.ownerId
          or (throw "Restic VM backups require a declared UUID or ownerId.")
      }";
      modules = [
        configurations.modules.${target.configuration}
        ({ config, lib, ... }: {
          networking.hostName = lib.mkForce target.guestHostName;
          nixberry.vm.rootFilesystem = target.rootFilesystem;
          nixberry.vm.network = target.network or { };
          nixberry.vm.adapters = guestAdapters target;
          nixberry.vm.security = builtins.removeAttrs target.security [ "imageDirectory" ];
          assertions = [
            {
              assertion =
                config.nixberry.vm.rootFilesystem == target.rootFilesystem
                && config.fileSystems."/".fsType == target.rootFilesystem;
              message = "The selected configuration must preserve the root filesystem declared in the instance.";
            }
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
      import (../. + "/${target.backend}/package.nix") {
        pkgs = vmPkgs;
        spec = target.spec;
        inherit (target) guestHostName allowReset rootFilesystem;
        network = target.network or { };
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
        ./image.nix
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
in
{
  inherit
    defaultVmSystems
    genericInstallers
    vmSystems
    vmInstallers
    mkVmTools
    vmTools
    mkVmImage
    mkHypervImage
    mkPortableImage
    vmImages
    ;
}
