# Source-owned encrypted upload; storage enrollment is an explicit runtime action.
args@{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.nixberry.backup;
  r = cfg.restic;
  inherit (lib) mkOption types;
  tools = import ../../../management/deployment/capture-package.nix { inherit pkgs; };
  backupPolicy = {
    version = 1;
    sourceId = args.nixberryTargetIdentity or config.networking.hostName;
    inherit (cfg) captureMethod;
    capture = config.system.build.nixberryCapturePolicy;
    inherit (r)
      repository
      passwordFile
      repositoryIdFile
      recoveryFile
      runtimeDirectory
      timeoutSeconds
      ssh
      ;
  };
  maintenancePolicy = {
    version = 1;
    inherit (r) keepLast maxAgeSeconds;
  };
  maintenanceService = action: additionalAfter: {
    description =
      if action == "prune" then
        "Prune source-owned Restic backups"
      else
        "Check source-owned Restic backup freshness";
    wants = [ "network-online.target" ];
    after = [
      "network-online.target"
      "nixberry-backup.service"
    ]
    ++ additionalAfter;
    restartIfChanged = false;
    serviceConfig = {
      Type = "oneshot";
      User = "root";
      Group = "root";
      ExecStart = "${tools.restic}/bin/nixberry-restic backup-${action} ${
        if action == "prune" then "--apply" else "--json"
      }";
      UMask = "0077";
      RuntimeDirectory = lib.removePrefix "/run/" r.runtimeDirectory;
      RuntimeDirectoryMode = "0700";
      TemporaryFileSystem = [ "${r.runtimeDirectory}:rw,nosuid,nodev,noexec,size=512M,mode=0700" ];
      MemoryMax = "1G";
      MemorySwapMax = 0;
      PrivateTmp = true;
      NoNewPrivileges = true;
      TimeoutStartSec = r.timeoutSeconds + 240;
      TimeoutStopSec = "5min";
    };
  };
  maintenanceTimer = action: schedule: {
    description = "Schedule source-owned Restic backup ${action}";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = schedule;
      Persistent = r.persistent;
      Unit = "nixberry-backup-${action}.service";
    };
  };
in
{
  options.nixberry.backup.restic = {
    enable = lib.mkEnableOption "source-owned encrypted Restic backups";
    repository = mkOption {
      type = types.str;
      default = "";
      description = "Absolute local repository or sftp:USER@HOST:/PATH.";
    };
    passwordFile = mkOption {
      type = types.str;
      default = "/var/lib/nixberry-backup/password";
      description = "Private runtime repository password file; never secret contents.";
    };
    repositoryIdFile = mkOption {
      type = types.str;
      default = "/var/lib/nixberry-backup/repository-id";
      description = "Runtime repository identity pin written by explicit initialization.";
    };
    recoveryFile = mkOption {
      type = types.str;
      default = "/var/lib/nixberry-backup/recovery.tar";
      description = "Private runtime recipe archive included and authenticated with application data.";
    };
    runtimeDirectory = mkOption {
      type = types.str;
      default = "/run/nixberry-backup";
      description = "Private tmpfs directory for metadata and Restic temporary packs.";
    };
    timeoutSeconds = mkOption {
      type = types.ints.between 1 86400;
      default = 3600;
      description = "Shared time budget for all Restic commands in one backup or verification operation.";
    };
    schedule = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = "Optional source systemd calendar; null leaves capture manual.";
    };
    keepLast = mkOption {
      type = types.nullOr (types.ints.between 1 100000);
      default = null;
      description = "Optional number of this source's backups to retain; null disables native pruning.";
    };
    maxAgeSeconds = mkOption {
      type = types.nullOr (types.ints.between 1 31536000);
      default = null;
      description = "Optional maximum age of the latest authenticated source capture; null disables freshness checks.";
    };
    pruneSchedule = mkOption {
      type = types.nullOr types.nonEmptyStr;
      default = null;
      description = "Optional source systemd calendar for pruning; requires keepLast.";
    };
    checkSchedule = mkOption {
      type = types.nullOr types.nonEmptyStr;
      default = null;
      description = "Optional source systemd calendar for freshness checks; requires maxAgeSeconds.";
    };
    persistent = mkOption {
      type = types.bool;
      default = false;
      description = "Opt in to catching up missed source backup and maintenance schedules.";
    };
    ssh = mkOption {
      default = null;
      description = "Required SFTP key and strict storage host-key policy.";
      type = types.nullOr (
        types.submodule {
          options = {
            port = mkOption {
              type = types.port;
              default = 22;
            };
            identityFile = mkOption { type = types.str; };
            knownHostsFile = mkOption { type = types.str; };
          };
        }
      );
    };
  };
  config = lib.mkIf (cfg.enable && r.enable) {
    assertions = [
      {
        assertion = r.repository != "" && builtins.match "/run/[a-z][a-z0-9-]*" r.runtimeDirectory != null;
        message = "Native Restic requires a repository and a dedicated /run/NAME runtime directory.";
      }
      {
        assertion = r.pruneSchedule == null || r.keepLast != null;
        message = "Restic pruneSchedule requires keepLast.";
      }
      {
        assertion = r.checkSchedule == null || r.maxAgeSeconds != null;
        message = "Restic checkSchedule requires maxAgeSeconds.";
      }
    ];
    environment.systemPackages = [ tools.restic ];
    environment.etc."nixberry/backup.json".text = builtins.toJSON backupPolicy;
    environment.etc."nixberry/maintenance.json".text = builtins.toJSON maintenancePolicy;
    system.build.nixberryBackupPolicy = backupPolicy;
    system.build.nixberryBackupMaintenancePolicy = maintenancePolicy;
    system.build.nixberryBackupTool = tools.restic;
    systemd.services.nixberry-backup = {
      description = "Back up configuration-owned application data with Restic";
      wants = [ "network-online.target" ];
      requires = [ "nixberry-capture-recovery.service" ];
      after = [
        "network-online.target"
        "nixberry-capture-recovery.service"
      ];
      restartIfChanged = false;
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${tools.restic}/bin/nixberry-restic backup --apply --json";
        ExecStopPost = "${tools.capture}/bin/nixberry-capture resume --apply --json";
        UMask = "0077";
        RuntimeDirectory = lib.removePrefix "/run/" r.runtimeDirectory;
        RuntimeDirectoryMode = "0700";
        TemporaryFileSystem = [ "${r.runtimeDirectory}:rw,nosuid,nodev,noexec,size=512M,mode=0700" ];
        MemoryMax = "1G";
        MemorySwapMax = 0;
        PrivateTmp = true;
        NoNewPrivileges = true;
        TimeoutStartSec = r.timeoutSeconds + cfg.captureTimeoutSeconds + 240;
        TimeoutStopSec = "5min";
      };
    };
    systemd.timers.nixberry-backup = lib.mkIf (r.schedule != null) {
      description = "Schedule configuration-owned application backups";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = r.schedule;
        Persistent = r.persistent;
        Unit = "nixberry-backup.service";
      };
    };
    systemd.services.nixberry-backup-prune = lib.mkIf (r.keepLast != null) (
      maintenanceService "prune" [ "nixberry-capture-recovery.service" ]
    );
    systemd.services.nixberry-backup-check = lib.mkIf (r.maxAgeSeconds != null) (
      maintenanceService "check" [ ]
    );
    systemd.timers.nixberry-backup-prune = lib.mkIf (r.keepLast != null && r.pruneSchedule != null) (
      maintenanceTimer "prune" r.pruneSchedule
    );
    systemd.timers.nixberry-backup-check = lib.mkIf (
      r.maxAgeSeconds != null && r.checkSchedule != null
    ) (maintenanceTimer "check" r.checkSchedule);
  };
}
