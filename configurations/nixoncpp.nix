# Shared base and the upstream NixonCpp flake package.
{ pkgs, nixoncpp, ... }:
{
  imports = [
    ../modules/common.nix
    ../modules/applications/emcx
    ../modules/applications/hstr
  ];
  environment.systemPackages = [
    nixoncpp.packages.${pkgs.stdenv.hostPlatform.system}.default
  ];
}
