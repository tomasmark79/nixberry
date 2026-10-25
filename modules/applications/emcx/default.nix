# Extended Commander with its upstream customizations and previews.
{
  pkgs,
  lib,
  emcx,
  ...
}:
let
  package = emcx.packages.${pkgs.stdenv.hostPlatform.system}.emcx;
in
{
  environment.systemPackages = [
    package
    pkgs.ebook_tools
    pkgs.cdrtools
  ];
  programs.bash.interactiveShellInit = lib.mkAfter ''
    # The wrapper leaves the shell in the last directory visited in emcx.
    alias emcx='. "${package}/libexec/emcx/emcx-wrapper.sh"'
  '';
}
