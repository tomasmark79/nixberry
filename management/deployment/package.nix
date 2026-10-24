{ pkgs }:
let
  version = pkgs.lib.removeSuffix "\n" (builtins.readFile ../../VERSION);
  registry = import ./.;
  # Tests and local Python caches must not change the resulting management tool.
  source = import ../with-locales.nix {
    inherit pkgs;
    source = pkgs.lib.fileset.toSource {
      root = ./.;
      fileset = pkgs.lib.fileset.unions [
        ./cli.py
        ./dispatch.py
        ./discovery.py
        ./runtime.py
        ./scaffold.py
        ./security.py
        ./exporter.py
        ./guest-install.sh
        ./updater.py
        ./recipe_source.py
        ./diffing.py
        ./closure_metadata.py
        ./messages.py
        ./messages.sh
        ./locales/cs.sh
        ./inspection.py
        ./cleanup.py
        ./portable.py
        ./host_agent.py
        ./reset.py
      ];
    };
  };
  # Command listings must not force evaluation of systems, ISOs or disk images.
  lazyCommand =
    name: backend: action:
    let
      binary = "nixberry-${if backend == "libvirt" then "vm" else backend}-${action}";
      expected = pkgs.writeText "nixberry-dispatch-target.json" (
        builtins.toJSON registry.instances.${name}
      );
    in
    pkgs.writeShellApplication {
      name = binary;
      runtimeInputs = [
        pkgs.python3
        pkgs.nix
      ];
      text = ''
        export PYTHONPATH=${source}
        export NIXBERRY_DISPATCH_TARGET=${expected}
        exec python3 ${source}/dispatch.py ${pkgs.lib.escapeShellArg name} ${pkgs.lib.escapeShellArg binary} "$@"
      '';
    };
  portableProbe =
    target:
    let
      manifest = pkgs.writeText "nixberry-probe.json" (builtins.toJSON { inherit target; });
    in
    pkgs.writeShellApplication {
      name = "nixberry-${target.backend}-probe";
      runtimeInputs = [
        pkgs.python3
        pkgs.openssh
      ];
      text = ''
        export PYTHONPATH=${source}
        export NIXBERRY_PORTABLE_MANIFEST=${manifest}
        exec python3 ${source}/portable.py probe "$@"
      '';
    };
  resetCommand = target: import ./reset-package.nix { inherit pkgs target; };
  commands = builtins.mapAttrs (
    name: target:
    let
      backend = target.backend;
      probe =
        if backend == "libvirt" then
          import ../libvirt/probe-package.nix {
            inherit pkgs;
            inherit (target) spec;
          }
        else if backend == "hyperv" then
          import ../hyperv/probe-package.nix {
            inherit pkgs;
            inherit (target) spec;
          }
        else
          portableProbe target;
    in
    {
      probe = "${probe}/bin/nixberry-${if backend == "libvirt" then "libvirt" else backend}-probe";
      reset = "${resetCommand target}/bin/nixberry-reset";
    }
    // builtins.listToAttrs (
      map
        (action: {
          name = action;
          value = "${lazyCommand name backend action}/bin/nixberry-${
            if backend == "libvirt" then "vm" else backend
          }-${action}";
        })
        ([
          "create"
          "install"
          "provision"
        ])
    )
  ) (pkgs.lib.filterAttrs (_: target: !target.offline) registry.instances);
  manifest = pkgs.writeText "nixberry-deployment.json" (
    builtins.toJSON (
      builtins.mapAttrs (
        name: target:
        let
          targetCommands = if target.capabilities.hypervisor then commands.${name} or { } else { };
        in
        target
        // {
          commands = targetCommands;
          operations = import ./operations.nix target;
        }
      ) registry.targets
    )
  );
  hosts = pkgs.writeText "nixberry-deployment-hosts.json" (builtins.toJSON registry.hosts);
in
(pkgs.writeShellApplication {
  name = "nixberry";
  runtimeInputs = [
    pkgs.python3
    pkgs.libvirt
    pkgs.openssh
    pkgs.nix
    pkgs.nvd
    pkgs.qemu
    pkgs.openssh
  ];
  text = ''
    export NIXBERRY_VERSION=${pkgs.lib.escapeShellArg version}
    export NIXBERRY_DEPLOYMENT=${manifest}
    export NIXBERRY_DEPLOYMENT_HOSTS=${hosts}
    export PYTHONPATH=${source}
    exec python3 ${source}/cli.py "$@"
  '';
  passthru = { inherit manifest; };
}).overrideAttrs
  (_: {
    pname = "nixberry";
    inherit version;
    name = "nixberry-${version}";
  })
