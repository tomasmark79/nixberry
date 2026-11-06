# Encrypted SFTP upload and source-independent readback on isolated test machines.
{ pkgs }:
let
  tools = import ../management/deployment/capture-package.nix { inherit pkgs; };
  source = snapshot: { config, ... }: {
    imports = [ ../modules/services/backup ];
    virtualisation.memorySize = 2048;
    virtualisation.emptyDiskImages = if snapshot then [ 1024 ] else [ ];
    environment.systemPackages = [
      pkgs.btrfs-progs
      pkgs.openssh
      pkgs.restic
    ];
    nixberry.backup = {
      enable = true;
      captureMethod = if snapshot then "snapshot" else "cold";
      minFreeBytes = 1024 * 1024;
      roots.system =
        if snapshot then
          {
            path = "/data/live";
            snapshotDirectory = "/data/snapshots";
          }
        else
          {
            path = "/";
            snapshotDirectory = "/.snapshots/nixberry";
          };
      applications.web = {
        paths = [ (if snapshot then "/data/live/web" else "/var/lib/web") ];
        stopUnits = [ "writer.service" ];
      };
      restic = {
        enable = true;
        schedule = "2099-01-01 00:00:00";
        repository = "sftp:root@storage:/backup/${config.networking.hostName}";
        ssh = {
          port = 22;
          identityFile = "/var/lib/nixberry-backup/ssh-key";
          knownHostsFile = "/var/lib/nixberry-backup/known_hosts";
        };
      };
    };
    systemd.services.writer = {
      script = ''
        while true; do
          date +%s%N > ${if snapshot then "/data/live/web" else "/var/lib/web"}/counter.next
          mv ${if snapshot then "/data/live/web" else "/var/lib/web"}/counter.next ${
            if snapshot then "/data/live/web" else "/var/lib/web"
          }/counter
          sleep 0.1
        done
      '';
    };
  };
in
pkgs.testers.runNixOSTest {
  name = "nixberry-native-restic";
  nodes = {
    btrfs = source true;
    cold = source false;
    storage = { ... }: {
      services.openssh.enable = true;
      services.openssh.settings = {
        PasswordAuthentication = false;
        PermitRootLogin = "prohibit-password";
      };
      environment.systemPackages = [
        tools.restic
        (import ../management/deployment/package.nix { inherit pkgs; })
        pkgs.openssh
        pkgs.restic
      ];
      virtualisation.memorySize = 2048;
      users.users.recovery.isNormalUser = true;
    };
  };
  testScript = ''
    import json, shlex
    start_all()
    for node in [btrfs, cold, storage]:
        node.wait_for_unit("multi-user.target")
        node.succeed("NIXBERRY_LANG=cs nixberry-restic --help | grep -F 'Zálohování dat aplikací'")
        node.succeed("mkdir -m 700 -p /var/lib/nixberry-backup /run/nixberry-backup; printf test-only-password > /var/lib/nixberry-backup/password; chmod 600 /var/lib/nixberry-backup/password")
        node.succeed("ssh-keygen -q -t ed25519 -N \"\" -f /var/lib/nixberry-backup/ssh-key")
    storage.succeed("mkdir -m 700 -p /root/.ssh /backup")
    storage.succeed("install -d -m 700 -o recovery -g users /run/recovery-operator; runuser -u recovery -- ssh-keygen -q -t ed25519 -N \"\" -f /home/recovery/ssh-key")
    recovery_key = storage.succeed("cat /home/recovery/ssh-key.pub").strip()
    storage.succeed("printf '%s\\n' 'restrict,command=\"internal-sftp -R\" " + recovery_key + "' >> /root/.ssh/authorized_keys; chmod 600 /root/.ssh/authorized_keys")
    host_key = storage.succeed("cat /etc/ssh/ssh_host_ed25519_key.pub").strip()
    for node in [btrfs, cold, storage]:
        public_key = node.succeed("cat /var/lib/nixberry-backup/ssh-key.pub").strip()
        storage.succeed("printf '%s\\n' '" + public_key + "' >> /root/.ssh/authorized_keys; chmod 600 /root/.ssh/authorized_keys")
        node.succeed("printf '%s\\n' 'storage " + host_key + "' > /var/lib/nixberry-backup/known_hosts")
    btrfs.succeed("mkfs.btrfs -f /dev/vdb; mkdir /data; mount /dev/vdb /data; btrfs subvolume create /data/live; mkdir /data/live/web")
    cold.succeed("mkdir -p /var/lib/web")
    for node, data in [(btrfs, "/data/live/web"), (cold, "/var/lib/web")]:
        node.succeed("printf only-encrypted-application-data > " + data + "/example; chmod 600 " + data + "/example")
        # Receive a sealed recipe fixture through the installed binary stdin protocol.
        fixture = "import hashlib,json,pathlib,recovery_recipe; p=pathlib.Path('/tmp/recovery-fixture'); p.mkdir(); [(p/n).write_text('{}' if n == 'flake.lock' else 'test-only') for n in ('flake.nix','flake.lock','admin.pub')]; v=json.loads(pathlib.Path('/etc/nixberry/backup.json').read_text()); m={'format':recovery_recipe.FORMAT,'sourceId':v['sourceId'],'target':'vm-demo-guest','systemPath':str(pathlib.Path('/run/current-system').resolve()),'policySha256':hashlib.sha256(recovery_recipe.encode(v).encode()).hexdigest()}; recovery_recipe.pack(p,m,'/tmp/received-recovery.tar')"
        node.succeed("env PYTHONPATH=${tools.source} ${pkgs.python3}/bin/python3 -c " + shlex.quote(fixture))
        checksum = node.succeed("sha256sum /tmp/received-recovery.tar").split()[0]
        node.succeed("install -m 600 /tmp/received-recovery.tar /var/lib/nixberry-backup/recovery.tar; nixberry-restic backup-stage --system $(readlink -f /run/current-system) --apply --json")
        node.fail("nixberry-restic backup-stage --system $(readlink -f /run/current-system) --receive-sha256 " + "0" * 64 + " --apply --json < /tmp/received-recovery.tar")
        received = json.loads(node.succeed("nixberry-restic backup-stage --system $(readlink -f /run/current-system) --receive-sha256 " + checksum + " --apply --json < /tmp/received-recovery.tar"))
        assert received["archiveSha256"] == checksum
        node.succeed("systemctl start writer.service")
        node.wait_until_succeeds("test -s " + data + "/counter")
        node.succeed("nixberry-restic backup-init --json")
        node.succeed("test ! -e /var/lib/nixberry-backup/repository-id")
        node.succeed("nixberry-restic backup-init --apply --json")
        node.succeed("nixberry-restic backup --json; systemctl is-active writer.service")
        assert json.loads(node.succeed("nixberry-restic backups --json"))["snapshots"] == []
        report = json.loads(node.succeed("nixberry-restic backup --apply --json"))
        assert report["state"] == "recorded-captured"
        node.succeed("systemctl is-active writer.service; test ! -e /var/lib/nixberry-capture/pending.json")
        listed = json.loads(node.succeed("nixberry-restic backups --json"))["snapshots"]
        assert [row["snapshotId"] for row in listed] == [report["snapshotId"]]
        # Exercise the actual source service, including its bounded private tmpfs.
        node.succeed("systemctl start nixberry-backup.service")
        node.succeed("systemctl is-active writer.service; test ! -e /var/lib/nixberry-capture/pending.json")
        assert len(json.loads(node.succeed("nixberry-restic backups --json"))["snapshots"]) == 2
        # Trigger the installed timer without waiting for its distant test calendar.
        node.succeed("mkdir -p /run/systemd/system/nixberry-backup.timer.d; printf '[Timer]\\nOnCalendar=\\nOnActiveSec=1s\\nAccuracySec=1us\\n' > /run/systemd/system/nixberry-backup.timer.d/test.conf; systemctl daemon-reload; systemctl restart nixberry-backup.timer")
        node.wait_until_succeeds("test $(nixberry-restic backups --json | ${pkgs.jq}/bin/jq '.snapshots | length') = 3")
        node.succeed("systemctl stop nixberry-backup.timer")
        node.wait_until_succeeds("test $(systemctl show nixberry-backup.service -p ActiveState --value) = inactive")
        node.succeed("systemctl is-active writer.service; test ! -e /var/lib/nixberry-capture/pending.json")
        # Strict host trust must fail before shutting down the writer.
        bad_key = node.succeed("cat /var/lib/nixberry-backup/ssh-key.pub").strip()
        node.succeed("printf '%s\\n' 'storage " + bad_key + "' > /var/lib/nixberry-backup/known_hosts")
        node.fail("nixberry-restic backup --apply --json")
        node.succeed("systemctl is-active writer.service")
        node.succeed("printf '%s\\n' 'storage " + host_key + "' > /var/lib/nixberry-backup/known_hosts")
        # Recovery runs on storage with its own SSH key and no source data/config.
        policy = node.succeed("cat /etc/nixberry/backup.json").strip()
        pin = node.succeed("cat /var/lib/nixberry-backup/repository-id").strip()
        storage.succeed("printf '%s' '" + policy + "' > /run/recovery-policy.json")
        storage.succeed("printf '%s\\n' '" + pin + "' > /var/lib/nixberry-backup/repository-id; chmod 600 /var/lib/nixberry-backup/repository-id")
        node.shutdown()
        verified = json.loads(storage.succeed("nixberry-restic --config /run/recovery-policy.json verify-backup --snapshot " + report["snapshotId"] + " --json"))
        assert verified["state"] == "recorded-verified"
        # A non-root operator has independent credentials, no declaration and no
        # source capture catalogue. The source is already powered off here.
        access = {"version": 1, "target": "vm-demo-guest", "sourceId": json.loads(policy)["sourceId"],
                  "repository": json.loads(policy)["repository"], "repositoryId": pin,
                  "passwordFile": "/home/recovery/password", "runtimeDirectory": "/run/recovery-operator",
                  "timeoutSeconds": 120, "ssh": {"port": 22, "identityFile": "/home/recovery/ssh-key",
                  "knownHostsFile": "/home/recovery/known_hosts"}}
        for name, contents in [("access.json", json.dumps(access)), ("password", "test-only-password"),
                               ("known_hosts", "storage " + host_key)]:
            storage.succeed("printf '%s' " + shlex.quote(contents) + " > /home/recovery/" + name + "; chown recovery:users /home/recovery/" + name + "; chmod 600 /home/recovery/" + name)
        command = "runuser -u recovery -- nixberry "
        options = " vm-demo-guest --backup-access /home/recovery/access.json --json"
        rows = json.loads(storage.succeed(command + "backups" + options))["snapshots"]
        assert len(rows) == 3
        checked = json.loads(storage.succeed(command + "verify-backup" + options + " --snapshot " + report["snapshotId"]))
        assert checked["state"] == "recorded-verified"
        destination = "/home/recovery/recovered-" + node.name
        recover = command + "recover-source" + options + " --snapshot " + report["snapshotId"] + " --output " + destination
        assert json.loads(storage.succeed(recover))["applied"] is False
        storage.succeed("test ! -e " + destination)
        assert json.loads(storage.succeed(recover + " --apply"))["state"] == "recorded-source-recovered"
        storage.succeed("test ! -e " + destination + "/INCOMPLETE; grep -Fx test-only " + destination + "/recipe/admin.pub")
        storage.fail(recover + " --apply")
  '';
}
