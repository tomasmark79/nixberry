{
  pkgs,
  installerIso,
  isoName,
  targetSystem,
  diskoScript,
  spec,
  guestHostName,
  image ? null,
  deploymentMethod ? "image",
  allowReset ? false,
}:
let
  commonSource = import ../messages.nix { inherit pkgs; };
  probeSpec = pkgs.writeText "nixberry-hyperv-spec.json" (builtins.toJSON spec);
  manifest = pkgs.writeText "nixberry-hyperv-manifest.json" (
    builtins.toJSON (
      spec
      // {
        inherit deploymentMethod;
        isoPath = if deploymentMethod == "iso" then "${installerIso}/iso/${isoName}" else null;
        imagePath = if deploymentMethod == "image" then "${image}/system.vhdx" else null;
        systemPath = toString targetSystem;
        diskoPath = toString diskoScript;
        inherit guestHostName;
      }
    )
  );
  command =
    mode:
    pkgs.writeShellApplication {
      name = "nixberry-hyperv-${mode}";
      runtimeInputs = [
        pkgs.python3
        pkgs.openssh
      ];
      text = ''
        export NIXBERRY_HYPERV_MANIFEST=${manifest}
        export NIXBERRY_HYPERV_MODULE=${./probe.py}
        export NIXBERRY_HYPERV_REMOTE=${./host.ps1}
        export NIXBERRY_HYPERV_GUEST=${./guest-install.sh}
        export PYTHONPATH=${commonSource}:${
          pkgs.lib.fileset.toSource {
            root = ./.;
            fileset = ./policy.py;
          }
        }
        exec python3 ${./manage.py} ${mode} "$@"
      '';
    };
  probe = import ./probe-package.nix { inherit pkgs spec; };
  reset = import ../deployment/reset-package.nix {
    inherit pkgs;
    commandName = "nixberry-hyperv-reset";
    target = {
      inherit spec allowReset;
      backend = "hyperv";
    };
  };
in
pkgs.symlinkJoin {
  name = "nixberry-hyperv-tools";
  paths = [
    probe
    reset
    (command "create")
    (command "install")
    (command "provision")
  ];
  passthru = {
    inherit
      manifest
      probeSpec
      reset
      probe
      ;
  };
}
