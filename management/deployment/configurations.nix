# Konfigurace jsou běžné NixOS moduly; jejich seznam nevyhodnocuje systémy.
let
  directory = ../../configurations;
  entries = builtins.readDir directory;
  files = builtins.filter (
    file:
    (entries.${file} == "regular" && builtins.match "[a-z][a-z0-9-]*\\.nix" file != null)
    || (
      entries.${file} == "directory"
      && builtins.match "[a-z][a-z0-9-]*" file != null
      && builtins.pathExists (directory + "/${file}/default.nix")
      && (builtins.readDir (directory + "/${file}"))."default.nix" == "regular"
    )
  ) (builtins.attrNames entries);
  definitions = map (file: {
    name =
      if entries.${file} == "directory" then
        file
      else
        builtins.substring 0 (builtins.stringLength file - 4) file;
    value = directory + "/${file}";
  }) files;
  modules = builtins.listToAttrs definitions;
in
assert
  builtins.length definitions == builtins.length (builtins.attrNames modules)
  || throw "Konfigurace má současně soubor i adresář; ponech pouze jednu definici.";
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
      throw "Neznámá configuration. Dostupné konfigurace: ${builtins.concatStringsSep ", " (builtins.attrNames modules)}.";
}
