# Shared base and the upstream NixonCpp flake package.
{ pkgs, nixoncpp, ... }:
{
  imports = [ ../modules/common.nix ];
  environment.systemPackages = [
    nixoncpp.packages.${pkgs.stdenv.hostPlatform.system}.default
  ];
}
