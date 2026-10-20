# Configurations are ordinary NixOS modules; listing them does not evaluate systems.
let
  directory = ../../configurations;
  entries = builtins.readDir directory;
  files = builtins.filter (
    file:
    (entries.${file} == "regular" && builtins.match "[a-z][a-z0-9-]*\\.nix" file != null)
    || (
      (entries.${file} == "directory" || entries.${file} == "symlink")
      && builtins.match "[a-z][a-z0-9-]*" file != null
      && builtins.pathExists (directory + "/${file}/default.nix")
      && (builtins.readDir (directory + "/${file}"))."default.nix" == "regular"
    )
  ) (builtins.attrNames entries);
  definitions = map (file: {
    name =
      if entries.${file} != "regular" then
        file
      else
        builtins.substring 0 (builtins.stringLength file - 4) file;
    value = directory + "/${file}";
  }) files;
  modules = builtins.listToAttrs definitions;
in
assert
  builtins.length definitions == builtins.length (builtins.attrNames modules)
  || throw "The configuration has both a file and a directory; keep only one definition.";
{
  inherit modules;
  select =
    target:
    let
      name = target.configuration or "default";
    in
    if builtins.isString name && builtins.hasAttr name modules then
      name
    else
      throw "Unknown configuration. Available configurations: ${builtins.concatStringsSep ", " (builtins.attrNames modules)}.";
}
