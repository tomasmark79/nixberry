{
  pkgs,
  backend,
  spec,
  guestHostName,
  image,
  targetSystem,
  diskoScript,
  installerIso,
  isoName,
  deploymentMethod ? "image",
  allowReset ? false,
  security ? { mode = "none"; },
  securityTarget ? null,
  ...
}:
let
  source = import ../with-locales.nix {
    inherit pkgs;
    source = pkgs.lib.fileset.toSource {
    root = ../deployment;
    fileset = pkgs.lib.fileset.unions [
      ../deployment/portable.py
      ../deployment/host_agent.py
      ../deployment/guest-install.sh
      ../deployment/runtime.py
      ../deployment/messages.py
      ../deployment/security.py
      ../deployment/messages.sh
      ../deployment/locales/cs.sh
    ];
    };
  };
  manifest = pkgs.writeText "nixberry-${backend}-manifest.json" (
    builtins.toJSON (
      spec
      // pkgs.lib.optionalAttrs (security.mode != "none") { inherit security securityTarget; }
      // {
        inherit backend guestHostName deploymentMethod;
        systemPath = toString targetSystem;
        diskoPath = toString diskoScript;
        imagePath = if deploymentMethod == "image" then toString image else null;
        isoPath = if deploymentMethod == "iso" then "${installerIso}/iso/${isoName}" else null;
        target = {
          inherit
            backend
            spec
            guestHostName
            deploymentMethod
            ;
        } // pkgs.lib.optionalAttrs (security.mode != "none") { inherit security; };
      }
    )
  );
  command =
    action:
    pkgs.writeShellApplication {
      name = "nixberry-${backend}-${action}";
      runtimeInputs = [
        pkgs.python3
        pkgs.openssh
      ];
      text = ''
        export NIXBERRY_PORTABLE_MANIFEST=${manifest}
        export PYTHONPATH=${source}
        exec python3 ${source}/portable.py ${action} "$@"
      '';
    };
  reset = import ../deployment/reset-package.nix {
    inherit pkgs;
    commandName = "nixberry-${backend}-reset";
    target = { inherit backend spec allowReset; };
  };
in
pkgs.symlinkJoin {
  name = "nixberry-${backend}-tools";
  paths = [
    reset
  ]
  ++ map command [
    "probe"
    "create"
    "install"
    "provision"
  ];
  passthru = { inherit manifest reset; };
}
