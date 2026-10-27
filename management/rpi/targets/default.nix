# Each RPi has its own file; an empty directory means no physical targets.
let
  files = builtins.readDir ./.;
  names = builtins.filter (
    name: name != "default.nix" && builtins.match ".*\\.nix" name != null && files.${name} == "regular"
  ) (builtins.attrNames files);
in
builtins.listToAttrs (
  map (file: {
    name = builtins.substring 0 (builtins.stringLength file - 4) file;
    value = import ../normalize-target.nix (import (./. + "/${file}"));
  }) names
)
