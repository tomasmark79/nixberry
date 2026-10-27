# Local metadata only: completion never invokes a provider, builds or contacts a guest.
let
  registry = import ./.;
  source = builtins.getEnv "NIXBERRY_COMPLETION_SOURCE";
  target = registry.targets.${source} or { };
  backup = target.backup or { enable = false; };
  repository = backup.repository or null;
  entries = if backup.enable && repository != null && builtins.pathExists repository
    then builtins.readDir repository else { };
  selected = builtins.filter (name:
    entries.${name} == "directory" && builtins.match "snapshot-[0-9a-f]{32}" name != null
    && builtins.pathExists (repository + "/${name}/receipt.json")
    && (builtins.readDir (repository + "/${name}"))."receipt.json" == "regular"
    && (let value = builtins.fromJSON (builtins.readFile (repository + "/${name}/receipt.json"));
        in (value.target or null) == source && (value.snapshotId or null) == builtins.substring 9 32 name
          && (value.verification.status or null) == "ok")
  ) (builtins.attrNames entries);
in builtins.concatStringsSep "\n" (map (name: builtins.substring 9 32 name) selected)
