# Verify writer holds on a disposable destination; no application restore occurs.
{ pkgs }:
let
  tools = import ../management/deployment/capture-package.nix { inherit pkgs; };
in
pkgs.testers.runNixOSTest {
  name = "nixberry-restore-preparation";
  nodes.machine = { ... }: {
    imports = [ ../modules/services/backup ];
    virtualisation.memorySize = 1024;
    systemd.tmpfiles.rules = [ "f /allow-writer 0600 root root -" ];
    systemd.packages = [
      (pkgs.writeTextDir "lib/systemd/system/writer.service.d/90-app-conditions.conf" ''
        [Unit]
        ConditionPathExists=
        ConditionPathExists=/allow-writer
      '')
    ];
    nixberry.backup = {
      enable = true;
      restore.prepare = true;
      applications.web = {
        paths = [ "/var/lib/restore-demo" ];
        stopUnits = [
          "writer-alias.service"
          "instance@one.service"
          "trigger.socket"
          "trigger.timer"
        ];
      };
      restic = {
        enable = true;
        repository = "/backup/test-only";
        schedule = "2099-01-01 00:00:00";
      };
    };
    systemd.services.writer = {
      aliases = [ "writer-alias.service" ];
      wantedBy = [ "multi-user.target" ];
      unitConfig.ConditionPathExists = "/allow-writer";
      script = ''
        mkdir -p /var/lib/restore-demo
        echo writer > /var/lib/restore-demo/writer
        sleep infinity
      '';
    };
    systemd.services."instance@".script = ''
      mkdir -p /var/lib/restore-demo
      echo instance > /var/lib/restore-demo/instance
      sleep infinity
    '';
    systemd.targets.multi-user.wants = [ "instance@one.service" ];
    systemd.sockets.trigger = {
      wantedBy = [ "sockets.target" ];
      listenStreams = [ "127.0.0.1:12345" ];
      socketConfig.Service = "writer.service";
    };
    systemd.timers.trigger = {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnBootSec = "1s";
        Unit = "writer.service";
      };
    };
  };
  testScript = ''
    import json, shlex
    machine.start(allow_reboot=True)
    machine.wait_for_unit("multi-user.target")
    machine.succeed("NIXBERRY_LANG=cs nixberry-restore-target --help | grep -F 'připravený cíl obnovy'")
    machine.succeed("touch /allow-writer")
    units = "writer-alias.service instance@one.service trigger.socket trigger.timer nixberry-backup.service nixberry-backup.timer"
    for unit in units.split():
        machine.succeed("systemctl start " + unit)
        machine.fail("systemctl is-active " + unit)
    machine.succeed("test ! -e /var/lib/restore-demo")
    ready = json.loads(machine.succeed("nixberry-restore-target status --json"))
    assert ready["ready"] is True and ready["completed"] is False
    assert len(ready["units"]) == 6
    machine.succeed("systemctl show instance@one.service -p ExecStart --value | grep -F instance")
    # Merely inspecting readiness never creates completion state or starts writers.
    machine.succeed("test -z \"$(find /var/lib/nixberry-restore -mindepth 1 -maxdepth 1)\"")
    identity = ready["machineId"]
    machine.reboot()
    machine.wait_for_unit("multi-user.target")
    again = json.loads(machine.succeed("nixberry-restore-target status --json"))
    assert again["ready"] is True and again["machineId"] == identity
    machine.succeed("test ! -e /var/lib/restore-demo; rm -f /allow-writer")
    # A synthetic root-owned completion record exercises the gate, not data recovery.
    complete = "import json,pathlib,snapshots,restore_target; p=restore_target.policy(json.loads(pathlib.Path('/etc/nixberry/restore.json').read_text())); snapshots.write_record(restore_target.marker_path(p),{'version':1,'machineId':snapshots.machine_id(),'policySha256':restore_target.policy_hash(p),'sourceId':'vm-demo-source','snapshotId':'d'*64,'systemPath':snapshots.running_system(),'completedAt':1})"
    machine.succeed("env PYTHONPATH=${tools.source} ${pkgs.python3}/bin/python3 -c " + shlex.quote(complete))
    # Existing unit conditions remain effective even after the restore gate opens.
    machine.succeed("systemctl start writer.service")
    machine.fail("systemctl is-active writer.service")
    machine.succeed("touch /allow-writer; systemctl start trigger.socket; systemctl start writer.service instance@one.service trigger.timer")
    for unit in ["writer.service", "instance@one.service", "trigger.socket", "trigger.timer"]:
        machine.succeed("systemctl is-active " + unit)
    machine.succeed("grep -Fx writer /var/lib/restore-demo/writer; grep -Fx instance /var/lib/restore-demo/instance")
    done = json.loads(machine.succeed("nixberry-restore-target status --json"))
    assert done["completed"] is True and done["ready"] is False
  '';
}
