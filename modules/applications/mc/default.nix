# Modern Midnight Commander with the same customizations and previews as Nixon.
{
  pkgs,
  lib,
  mc-modern,
  ...
}:
let
  mc = mc-modern.packages.${pkgs.stdenv.hostPlatform.system}.mc;
in
{
  environment.systemPackages = [
    mc
    pkgs.ebook_tools
    pkgs.cdrtools
  ];
  programs.bash.interactiveShellInit = lib.mkAfter ''
    # The wrapper leaves the shell in the last directory visited in MC.
    export MC_WRAPPER="${mc}/libexec/mc/mc-wrapper.sh"
    alias mc='. "$MC_WRAPPER"'
  '';
}
