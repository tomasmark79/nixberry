# A useful first server: an independent two-interface router with AdGuard Home.
{ pkgs, ... }:
{
  imports = [
    ../modules/common.nix
    ../modules/applications/emcx
    ../modules/applications/hstr
    ../modules/services/router.nix
  ];

  # Host tools; router services declare their own runtime dependencies.
  environment.systemPackages = with pkgs; [
    htop
    tmux
  ];
}
