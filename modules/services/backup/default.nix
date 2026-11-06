# Configurations own application consistency; platforms own the disk layout.
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.nixberry.backup;
  inherit (lib) mkOption types;
  capturePolicy = {
    version = 1;
    inherit (cfg)
      applications
      roots
      stateDirectory
      minFreeBytes
      ;
    timeoutSeconds = cfg.captureTimeoutSeconds;
  };
  tool = (import ../../../management/deployment/capture-package.nix { inherit pkgs; }).capture;
in
{
  imports = [ ./restic.nix ];
  options.nixberry.backup = {
    enable = lib.mkEnableOption "configuration-owned application capture and backups";
    captureMethod = mkOption {
      type = types.enum [
        "snapshot"
        "cold"
      ];
      default = if config.fileSystems."/".fsType == "btrfs" then "snapshot" else "cold";
      description = "Snapshot Btrfs or keep writers stopped during a direct backup; no staging copy.";
    };
    applications = mkOption {
      default = { };
      description = "Explicit application data and services which write it.";
      type = types.attrsOf (
        types.submodule {
          options = {
            paths = mkOption {
              type = types.listOf types.str;
              description = "Absolute data paths; include symbolic-link targets explicitly.";
            };
            stopUnits = mkOption {
              type = types.listOf types.str;
              default = [ ];
              description = "Writer services and their activating sockets/timers; an empty list explicitly permits live reads.";
            };
          };
        }
      );
    };
    roots = mkOption {
      description = "Source subvolumes and same-filesystem snapshot storage outside each source.";
      default.system = {
        path = "/";
        snapshotDirectory = "/.snapshots/nixberry";
      };
      type = types.attrsOf (
        types.submodule {
          options = {
            path = mkOption {
              type = types.str;
              description = "Source Btrfs subvolume root.";
            };
            snapshotDirectory = mkOption {
              type = types.str;
              description = "Private directory outside the source subvolume.";
            };
          };
        }
      );
    };
    stateDirectory = mkOption {
      type = types.str;
      default = "/var/lib/nixberry-capture";
      description = "Private persistent capture metadata and interrupted-writer recovery records.";
    };
    minFreeBytes = mkOption {
      type = types.ints.positive;
      default = 1024 * 1024 * 1024;
      description = "Minimum available source space before snapshot capture.";
    };
    captureTimeoutSeconds = mkOption {
      type = types.ints.between 1 3600;
      default = 120;
      description = "Bounded writer shutdown and snapshot creation; writer recovery has its own timeout.";
    };
  };
  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.applications != { };
        message = "Configuration-owned backups require an explicit application catalogue.";
      }
    ];
    environment.systemPackages = [ tool ];
    environment.etc."nixberry/capture.json".text = builtins.toJSON capturePolicy;
    system.build.nixberryCapturePolicy = capturePolicy;
    system.build.nixberryCaptureTool = tool;
    # Abrupt power loss leaves the source recovery list intact. Ordinary command
    # failures and handled signals resume writers in the capture process itself.
    systemd.services.nixberry-capture-recovery = {
      description = "Recover writers from an interrupted NixBerry capture";
      wantedBy = [ "multi-user.target" ];
      after = [ "local-fs.target" ];
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${tool}/bin/nixberry-capture resume --apply --json";
        UMask = "0077";
        TimeoutStartSec = "5min";
      };
    };
  };
}
