# Fresh local declarations; does not evaluate a flake or NixOS systems.
let
  registry = import ./.;
  operations = import ./operations.nix;
  action = builtins.getEnv "NIXBERRY_COMPLETION_ACTION";
  direct = builtins.getEnv "NIXBERRY_COMPLETION_DIRECT" == "1";
  names = builtins.filter (
    name:
    let
      target = registry.targets.${name};
      effective = target // {
        capabilities = target.capabilities // {
          system = target.capabilities.system || (direct && (target.offline or false));
        };
      };
    in
    builtins.elem action (operations effective)
  ) (builtins.attrNames registry.targets);
in
builtins.concatStringsSep "\n" names
