# Společný základ a hotový balíček NixonCpp z jeho vlastního flake.
{ pkgs, nixoncpp, ... }:
{
  imports = [ ../modules/common.nix ];
  environment.systemPackages = [
    nixoncpp.packages.${pkgs.stdenv.hostPlatform.system}.default
  ];
}
