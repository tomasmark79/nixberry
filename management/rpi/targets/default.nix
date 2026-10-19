# Každé RPi má vlastní soubor; prázdný adresář znamená žádné fyzické cíle.
let
  files = builtins.readDir ./.;
  names = builtins.filter (
    name: name != "default.nix" && builtins.match ".*\\.nix" name != null && files.${name} == "regular"
  ) (builtins.attrNames files);
in
builtins.listToAttrs (
  map (file: {
    name = builtins.substring 0 (builtins.stringLength file - 4) file;
    value = import (./. + "/${file}");
  }) names
)
