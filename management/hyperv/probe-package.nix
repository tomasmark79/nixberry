# Průzkum hostitele je nezávislý na obrazu a konfiguraci systému VM.
{ pkgs, spec }:
let
  commonSource = import ../messages.nix { inherit pkgs; };
  probeSpec = pkgs.writeText "nixberry-hyperv-spec.json" (builtins.toJSON spec);
in
pkgs.writeShellApplication {
  name = "nixberry-hyperv-probe";
  runtimeInputs = [
    pkgs.python3
    pkgs.openssh
  ];
  text = ''
    export NIXBERRY_HYPERV_SPEC=${probeSpec}
    export NIXBERRY_HYPERV_PROBE=${./probe.ps1}
    export NIXBERRY_HYPERV_STATUS=${./status.ps1}
    export PYTHONPATH=${commonSource}
    exec python3 ${./probe.py} "$@"
  '';
}
