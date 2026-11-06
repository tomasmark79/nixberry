# Both guest entry points share one packaged catalogue and capture implementation.
{ pkgs }:
let
  source = import ../with-locales.nix {
    inherit pkgs;
    source = pkgs.lib.fileset.toSource {
      root = ./.;
      fileset = pkgs.lib.fileset.unions [
        ./snapshots.py
        ./native_restic.py
        ./recovery_recipe.py
        ./messages.py
      ];
    };
  };
  entry =
    name: program: extra:
    pkgs.writeShellApplication {
      inherit name;
      runtimeInputs = [
        pkgs.python3
        pkgs.btrfs-progs
        pkgs.util-linux
        pkgs.systemd
      ]
      ++ extra;
      text = ''
        export PYTHONPATH=${source}
        exec python3 ${source}/${program} "$@"
      '';
    };
in
{
  inherit source;
  capture = entry "nixberry-capture" "snapshots.py" [ ];
  restic = entry "nixberry-restic" "native_restic.py" [
    pkgs.restic
    pkgs.openssh
  ];
}
