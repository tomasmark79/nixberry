# Default NixBerry server assembled from shared modules.
{ pkgs, ... }:
{
  imports = [
    ../modules/common.nix
    ../modules/applications/emcx
    ../modules/applications/hstr
  ];

  # Additional host tools belong to this configuration.
  environment.systemPackages = with pkgs; [
    htop
    tmux
  ];
}
