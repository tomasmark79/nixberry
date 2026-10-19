# Attach the same compiled catalogue to every Python entry point.
{ pkgs, source }:
pkgs.runCommand "nixberry-localized-source" { } ''
  cp -R ${source} "$out"
  chmod u+w "$out"
  mkdir -p "$out/locales"
  chmod u+w "$out/locales"
  ln -s ${import ./locales.nix { inherit pkgs; }}/cs "$out/locales/cs"
''
