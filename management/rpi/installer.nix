# Instalační SD zachovává hardware a síťový základ připnutého upstreamu.
{ lib, pkgs, ... }:
let
  commonSource = import ../messages.nix { inherit pkgs; };
in
{
  environment.systemPackages = [
    (pkgs.writeShellApplication {
      name = "nixberry-install-local";
      runtimeInputs = with pkgs; [
        python3
        bash
        coreutils
        jq
        util-linux
        nix
        iproute2
        gnused
        gnugrep
        gawk
        nixos-install-tools
      ];
      text = ''
        export NIXBERRY_RPI_MODULE=${./install.py}
        export NIXBERRY_RPI_PROBE=${./probe.sh}
        export NIXBERRY_RPI_GUEST=${./guest-install.sh}
        export NIXBERRY_RPI_JQ=${pkgs.jq}
        export PYTHONPATH=${commonSource}
        exec python3 ${./local-install.py} "$@"
      '';
    })
  ];
  services.openssh = {
    enable = true;
    settings = {
      PermitRootLogin = lib.mkForce "prohibit-password";
      PasswordAuthentication = lib.mkForce false;
      KbdInteractiveAuthentication = lib.mkForce false;
    };
  };
  boot.loader.raspberry-pi.bootloader = lib.mkForce "kernel";
  boot.zfs.forceImportRoot = false;
}
