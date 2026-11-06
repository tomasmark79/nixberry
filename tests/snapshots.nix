# Exercise real Btrfs capture and writer recovery only in a disposable test VM.
{ pkgs }:
pkgs.testers.runNixOSTest {
  name = "nixberry-snapshots";
  nodes.machine = { ... }: {
    imports = [ ../modules/services/backup ];
    virtualisation.emptyDiskImages = [ 1024 ];
    virtualisation.memorySize = 2048;
    environment.systemPackages = [
      pkgs.btrfs-progs
      pkgs.jq
    ];
    nixberry.backup = {
      enable = true;
      captureMethod = "snapshot";
      minFreeBytes = 1024 * 1024;
      applications.web = {
        paths = [ "/data/live/web" ];
        stopUnits = [
          "writer.service"
          "idle-writer.service"
        ];
      };
      roots.system = {
        path = "/data/live";
        snapshotDirectory = "/data/snapshots";
      };
    };
    systemd.services.writer = {
      script = ''
        while true; do
          date +%s%N > /data/live/web/value.next
          mv /data/live/web/value.next /data/live/web/value
          sleep 0.1
        done
      '';
    };
    systemd.services.idle-writer = {
      script = "sleep infinity";
    };
  };
  testScript = ''
    import json
    start_all()
    machine.wait_for_unit("multi-user.target")
    machine.succeed("mkfs.btrfs -f /dev/vdb; mkdir /data; mount /dev/vdb /data")
    machine.succeed("btrfs subvolume create /data/live; mkdir /data/live/web")
    machine.succeed("systemctl start writer.service")
    machine.wait_until_succeeds("test -s /data/live/web/value")
    before = machine.succeed("find /data -mindepth 1 -maxdepth 1 | sort")
    plan = json.loads(machine.succeed("nixberry-capture snapshot --json"))
    assert plan["applied"] is False
    assert machine.succeed("find /data -mindepth 1 -maxdepth 1 | sort") == before
    machine.succeed("systemctl is-active writer.service")
    report = json.loads(machine.succeed("nixberry-capture snapshot --apply --json"))
    snapshot = report["roots"]["system"]["snapshot"]
    machine.succeed("systemctl is-active writer.service")
    machine.fail("systemctl is-active idle-writer.service")
    assert machine.succeed("btrfs property get -ts " + snapshot + " ro").strip() == "ro=true"
    frozen = machine.succeed("cat " + snapshot + "/web/value")
    machine.wait_until_succeeds("test \"$(cat /data/live/web/value)\" != \"$(cat " + snapshot + "/web/value)\"")
    assert machine.succeed("cat " + snapshot + "/web/value") == frozen
    inventory = json.loads(machine.succeed("nixberry-capture snapshots --json"))
    assert [s["snapshotId"] for s in inventory["snapshots"]] == [report["snapshotId"]]
    # A nested data subvolume must not silently disappear from a root snapshot.
    machine.succeed("btrfs subvolume create /data/live/web/database")
    machine.fail("nixberry-capture snapshot --apply --json")
    machine.succeed("systemctl is-active writer.service")
    # Emulate a killed capture's durable writer recovery record.
    marker = json.dumps({"version": 1, "machineId": machine.succeed("cat /etc/machine-id").strip(),
                         "snapshotId": "a" * 32, "activeUnits": ["writer.service"]})
    machine.succeed("systemctl stop writer.service")
    machine.succeed("printf '%s' '" + marker + "' > /var/lib/nixberry-capture/pending.json; chmod 600 /var/lib/nixberry-capture/pending.json")
    machine.succeed("nixberry-capture resume --apply --json")
    machine.succeed("systemctl is-active writer.service; test ! -e /var/lib/nixberry-capture/pending.json")
    # Mounted references outside the snapshot directory must prevent deletion.
    machine.succeed("mkdir /snapshot-reference; mount --bind " + snapshot + " /snapshot-reference")
    machine.fail("nixberry-capture snapshot-remove --snapshot " + report["snapshotId"] + " --apply --json")
    machine.succeed("test -d " + snapshot + "; umount /snapshot-reference")
    removal = json.loads(machine.succeed("nixberry-capture snapshot-remove --snapshot " + report["snapshotId"] + " --json"))
    assert removal["applied"] is False
    machine.succeed("test -d " + snapshot)
    machine.succeed("nixberry-capture snapshot-remove --snapshot " + report["snapshotId"] + " --apply --json")
    machine.succeed("test ! -e " + snapshot)
    assert json.loads(machine.succeed("nixberry-capture snapshots --json"))["snapshots"] == []
    # Explicitly declared nested roots are captured in the same writer outage.
    policy = json.loads(machine.succeed("cat /etc/nixberry/capture.json"))
    policy["roots"]["database"] = {"path": "/data/live/web/database", "snapshotDirectory": "/data/snapshots"}
    machine.succeed("echo database-data > /data/live/web/database/record")
    machine.succeed("printf '%s' '" + json.dumps(policy) + "' > /run/capture-nested.json")
    command = "nixberry-capture --config /run/capture-nested.json "
    nested = json.loads(machine.succeed(command + "snapshot --apply --json"))
    assert set(nested["roots"]) == {"database", "system"}
    database = nested["roots"]["database"]["snapshot"]
    assert machine.succeed("cat " + database + "/record").strip() == "database-data"
    assert len(nested["paths"]) == 2
    machine.succeed(command + "snapshot-remove --snapshot " + nested["snapshotId"] + " --apply --json")
    # A source mounted by subvolume has an FSROOT prefix distinct from /.
    machine.succeed("btrfs subvolume delete /data/live/web/database; mkdir /source; mount -o subvol=live /dev/vdb /source")
    policy["roots"].pop("database")
    policy["roots"]["system"]["path"] = "/source"
    policy["applications"]["web"]["paths"] = ["/source/web"]
    machine.succeed("printf '%s' '" + json.dumps(policy) + "' > /run/capture-mounted.json")
    command = "nixberry-capture --config /run/capture-mounted.json "
    mounted = json.loads(machine.succeed(command + "snapshot --apply --json"))
    machine.succeed("test -s " + mounted["roots"]["system"]["snapshot"] + "/web/value")
    machine.succeed(command + "snapshot-remove --snapshot " + mounted["snapshotId"] + " --apply --json")
  '';
}
