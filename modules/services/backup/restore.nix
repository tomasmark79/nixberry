# The selected configuration owns destination paths and writer coverage.
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.nixberry.backup;
  r = cfg.restore;
  rules = builtins.fromJSON (builtins.readFile ../../../management/deployment/protected-units.json);
  units = lib.unique (
    lib.concatMap (app: if app.restoreUnits == null then [ ] else app.restoreUnits) (
      builtins.attrValues cfg.applications
    )
  );
  validUnit =
    unit:
    let
      parsed = builtins.match "([A-Za-z0-9_.@:-]+)\\.(service|socket|timer)" unit;
    in
    parsed != null
    && !(builtins.elem (builtins.head parsed) rules.management)
    && !(lib.any (prefix: lib.hasPrefix prefix unit) rules.prefixes);
  automatic = lib.optionals cfg.restic.enable (
    [ "nixberry-backup.service" ] ++ lib.optional (cfg.restic.schedule != null) "nixberry-backup.timer"
  );
  policy =
    assert lib.assertMsg (
      cfg.enable && cfg.applications != { }
    ) "Restore preparation requires an enabled application catalogue.";
    assert lib.assertMsg (lib.all (app: app.restoreUnits != null) (
      builtins.attrValues cfg.applications
    )) "Declare restoreUnits explicitly for applications captured without writer shutdown.";
    assert lib.assertMsg (lib.all validUnit units)
      "Restore preparation cannot hold networking or management units.";
    assert lib.assertMsg (lib.all
      (
        path:
        path != r.stateDirectory
        && !(lib.hasPrefix (path + "/") r.stateDirectory)
        && !(lib.hasPrefix (r.stateDirectory + "/") path)
      )
      (lib.concatMap (app: app.paths) (builtins.attrValues cfg.applications))
    ) "Restore state must stay outside application data paths.";
    assert lib.assertMsg (lib.all (part: part != "" && part != "." && part != "..") (
      builtins.tail (lib.splitString "/" r.stateDirectory)
    )) "Restore state requires a normalized absolute directory.";
    {
      version = 1;
      architecture = pkgs.stdenv.hostPlatform.system;
      stateDirectory = r.stateDirectory;
      automaticUnits = automatic;
      applications = lib.mapAttrs (_: app: {
        inherit (app) paths;
        units = app.restoreUnits;
      }) cfg.applications;
    };
  hash = builtins.hashString "sha256" (builtins.toJSON policy);
  condition = pkgs.writeText "nixberry-restore-condition" ''
    [Unit]
    ConditionPathExists=${r.stateDirectory}/completed-${hash}-%m.json
  '';
  guards = pkgs.linkFarm "nixberry-restore-conditions" (
    map (unit: {
      name = "lib/systemd/system/${unit}.d/zz-nixberry-restore.conf";
      path = condition;
    }) (lib.unique (units ++ automatic))
  );
  tool = (import ../../../management/deployment/capture-package.nix { inherit pkgs; }).restore;
in
{
  options.nixberry.backup.restore = {
    prepare = lib.mkEnableOption "hold destination writers until authenticated restoration completes";
    stateDirectory = lib.mkOption {
      type = lib.types.strMatching "/[A-Za-z0-9_./-]+";
      default = "/var/lib/nixberry-restore";
      description = "Private persistent destination state, outside application data; no systemd specifiers.";
    };
  };
  config = lib.mkIf r.prepare {
    system.build.nixberryRestorePolicy = policy;
    system.build.nixberryRestoreGuards = guards;
    environment.etc."nixberry/restore.json".text = builtins.toJSON policy;
    environment.systemPackages = [ tool ];
    systemd.packages = [ guards ];
    systemd.tmpfiles.rules = [ "d ${r.stateDirectory} 0700 root root -" ];
  };
}
