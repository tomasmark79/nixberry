# Reset only uses the declaration; it does not force system or media builds.
{
  pkgs,
  target,
  commandName ? "nixberry-reset",
}:
let
  common = import ../messages.nix { inherit pkgs; };
  manifest = pkgs.writeText "nixberry-reset-target.json" (builtins.toJSON target);
  source = pkgs.lib.fileset.toSource {
    root = ./.;
    fileset = pkgs.lib.fileset.unions [
      ./reset.py
      ./portable.py
      ./host_agent.py
      ./runtime.py
    ];
  };
  libvirtSource = pkgs.lib.fileset.toSource {
    root = ../libvirt;
    fileset = pkgs.lib.fileset.unions [
      ../libvirt/reset.py
      ../libvirt/policy.py
      ../libvirt/storage.py
    ];
  };
  libvirtManifest = pkgs.writeText "nixberry-reset-libvirt.json" (
    builtins.toJSON (
      target.spec
      // {
        allowReset = target.allowReset;
        diskPath = "${target.spec.storageDirectory}/${target.spec.volume}";
      }
    )
  );
in
pkgs.writeShellApplication {
  name = commandName;
  runtimeInputs = [
    pkgs.python3
    pkgs.openssh
  ]
  ++ pkgs.lib.optionals (target.backend == "libvirt") [
    pkgs.libvirt
    pkgs.qemu
  ];
  text =
    if target.backend == "libvirt" then
      ''
        export NIXBERRY_MANIFEST=${libvirtManifest}
        export PYTHONPATH=${common}:${libvirtSource}
        exec python3 ${libvirtSource}/reset.py "$@"
      ''
    else
      ''
        export NIXBERRY_RESET_TARGET=${manifest}
        export NIXBERRY_HYPERV_PROBE_MODULE=${../hyperv/probe.py}
        export NIXBERRY_HYPERV_POLICY_MODULE=${../hyperv/policy.py}
        export NIXBERRY_HYPERV_RESET=${../hyperv/reset.ps1}
        export PYTHONPATH=${common}:${source}
        exec python3 ${source}/reset.py "$@"
      '';
}
