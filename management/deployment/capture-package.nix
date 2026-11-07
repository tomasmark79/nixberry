# Guest entry points share one packaged catalogue and capture implementation.
{ pkgs }:
let
  source = import ../with-locales.nix {
    inherit pkgs;
    source = pkgs.lib.fileset.toSource {
      root = ./.;
      fileset = pkgs.lib.fileset.unions [
        ./snapshots.py
        ./unit_names.py
        ./protected-units.json
        ./restore_target.py
        ./restore_payload.py
        ./restore_apply.py
        ./restore_access.py
        ./restic_access.py
        ./backup_identity.py
        ./restic_backup.py
        ./restic_health.py
        ./restic_retention.py
        ./restic_maintenance.py
        ./recovery_recipe.py
        ./update_guard.py
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
  restore = entry "nixberry-restore-target" "restore_target.py" [
    pkgs.restic
    pkgs.openssh
    pkgs.rsync
    pkgs.coreutils
  ];
  update = entry "nixberry-update-guard" "update_guard.py" [ pkgs.nix ];
  restic = entry "nixberry-restic" "restic_backup.py" [
    pkgs.restic
    pkgs.openssh
  ];
}
