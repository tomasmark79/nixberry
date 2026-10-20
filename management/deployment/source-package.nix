# This bootstrap must remain independent of the target/configuration registry.
{ pkgs }:
let
  source = import ../with-locales.nix {
    inherit pkgs;
    source = pkgs.lib.fileset.toSource {
      root = ./.;
      fileset = pkgs.lib.fileset.unions [
        ./recipe_source.py
        ./messages.py
        ./messages.sh
        ./locales/cs.sh
      ];
    };
  };
in
pkgs.writeShellApplication {
  name = "nixberry-source";
  runtimeInputs = [
    pkgs.python3
    pkgs.nix
  ];
  text = ''
    export PYTHONPATH=${source}
    exec python3 ${source}/recipe_source.py "$@"
  '';
}
