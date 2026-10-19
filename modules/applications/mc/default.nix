# Modern Midnight Commander se stejnými úpravami a náhledy jako na Nixonu.
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
    # Wrapper po ukončení MC ponechá shell v naposledy procházeném adresáři.
    export MC_WRAPPER="${mc}/libexec/mc/mc-wrapper.sh"
    alias mc='. "$MC_WRAPPER"'
  '';
}
