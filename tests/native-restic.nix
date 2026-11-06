# Encrypted SFTP upload and source-independent readback on isolated test machines.
{ pkgs }:
let
  tools = import ../management/deployment/capture-package.nix { inherit pkgs; };
  nestedPath = "/data/live/web/database[1]?*\\raw name";
  source = snapshot: { config, lib, ... }: {
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
      roots.database = lib.mkIf snapshot {
        path = nestedPath;
        snapshotDirectory = "/data/snapshots";
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
    storage.succeed("install -m 600 /home/recovery/ssh-key /run/restore-readonly-key")
    storage.succeed("printf '%s\\n' 'restrict,command=\"internal-sftp -R\" " + recovery_key + "' >> /root/.ssh/authorized_keys; chmod 600 /root/.ssh/authorized_keys")
    host_key = storage.succeed("cat /etc/ssh/ssh_host_ed25519_key.pub").strip()
    for node in [btrfs, cold, storage]:
        public_key = node.succeed("cat /var/lib/nixberry-backup/ssh-key.pub").strip()
        storage.succeed("printf '%s\\n' '" + public_key + "' >> /root/.ssh/authorized_keys; chmod 600 /root/.ssh/authorized_keys")
        node.succeed("printf '%s\\n' 'storage " + host_key + "' > /var/lib/nixberry-backup/known_hosts")
    btrfs.succeed("mkfs.btrfs -f /dev/vdb; mkdir /data; mount /dev/vdb /data; btrfs subvolume create /data/live; mkdir /data/live/web")
    nested = ${builtins.toJSON nestedPath}
    sibling = "/data/live/web/database1Q-otherraw name"
    # The bind mount hides real parent data. A root snapshot alone exposes it.
    btrfs.succeed("mkdir " + shlex.quote(nested) + "; printf stale-underlay > " + shlex.quote(nested + "/hidden"))
    btrfs.succeed("btrfs subvolume create /data/database; printf live-child > /data/database/current; mount --bind /data/database " + shlex.quote(nested))
    btrfs.succeed("printf keep-sibling > " + shlex.quote(sibling))
    cold.succeed("mkdir -p /var/lib/web")
    for node, data in [(btrfs, "/data/live/web"), (cold, "/var/lib/web")]:
        node.succeed("printf only-encrypted-application-data > " + data + "/example; chmod 600 " + data + "/example")
        metadata_fixture = "import os,struct,pathlib; p=pathlib.Path(" + repr(data) + "); os.link(p/'example',p/'hardlink'); (p/'symlink').symlink_to('example'); os.setxattr(p/'example','user.nixberry-test',b'preserved'); acl=struct.pack('<I',2)+bytes().join(struct.pack('<HHI',t,m,i) for t,m,i in [(1,6,2**32-1),(2,4,12345),(4,0,2**32-1),(16,4,2**32-1),(32,0,2**32-1)]); os.setxattr(p/'example','system.posix_acl_access',acl); f=(p/'sparse').open('wb'); f.seek(8*1024*1024); f.write(b'end'); f.close()"
        node.succeed("${pkgs.python3}/bin/python3 -c " + shlex.quote(metadata_fixture))
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
        if node == btrfs:
            probe = "import json,pathlib,native_restic; v=json.loads(pathlib.Path('/etc/nixberry/backup.json').read_text()); r,p,m=native_restic.receipt(v," + repr(report["snapshotId"]) + "); paths={x['source']:x['payload'] for x in m['paths']}; rows=[json.loads(x) for x in r.run(['ls',p['payloadId'],'--json']).splitlines()]; print(json.dumps({'paths':[x['path'] for x in rows if x.get('struct_type')=='node'], 'parent':paths['/data/live/web'], 'child':r.run(['dump',p['payloadId'],paths[" + repr(nested) + "]+'/current']), 'sibling':r.run(['dump',p['payloadId'],paths['/data/live/web']+'/database1Q-otherraw name'])}))"
            tree = json.loads(node.succeed("env PYTHONPATH=${tools.source} ${pkgs.python3}/bin/python3 -c " + shlex.quote(probe)))
            assert tree["child"] == "live-child"
            assert tree["sibling"] == "keep-sibling"
            hidden = tree["parent"] + nested.removeprefix("/data/live/web")
            assert not any(path == hidden or path.startswith(hidden + "/") for path in tree["paths"]), "Hidden parent underlay leaked into encrypted payload"
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
        identity_probe = "import json,native_restic; v=json.load(open('/run/recovery-policy.json')); _,_,m=native_restic.receipt(v," + repr(report["snapshotId"]) + "); print(json.dumps({'format':m['format'],'identity':m['identity']}))"
        metadata = json.loads(storage.succeed("env PYTHONPATH=${tools.source} ${pkgs.python3}/bin/python3 -c " + shlex.quote(identity_probe)))
        assert metadata["format"] == "nixberry-restic-v2"
        assert metadata["identity"]["architecture"] == "x86_64-linux"
        assert {"name": "root", "uid": 0, "gid": 0} in metadata["identity"]["users"]
        assert {"name": "root", "gid": 0} in metadata["identity"]["groups"]
        assert all(set(row) == {"name", "uid", "gid"} for row in metadata["identity"]["users"])

        # Restore through native Restic into private staging after source loss.
        stage_probe = "import json,pathlib,backup_identity,native_restic,restore_payload; v=json.load(open('/run/recovery-policy.json')); v['ssh']['identityFile']='/run/restore-readonly-key'; r=native_restic.Restic(v,read_only=True); out=restore_payload.stage(r," + repr(report["snapshotId"]) + ",'/var/lib/restored-" + node.name + "',backup_identity.collect()); print(json.dumps(out))"
        staged = json.loads(storage.succeed("env PYTHONPATH=${tools.source} ${pkgs.python3}/bin/python3 -c " + shlex.quote(stage_probe)))
        assert staged["state"] == "staged-verified"
        # A name collision used only by the ACL must reject the private stage.
        storage.succeed("${pkgs.shadow}/bin/useradd --uid 12345 --gid users --no-create-home restore-collision")
        collision = stage_probe.replace("/var/lib/restored-" + node.name, "/var/lib/conflict-" + node.name)
        storage.fail("env PYTHONPATH=${tools.source} ${pkgs.python3}/bin/python3 -c " + shlex.quote(collision))
        storage.succeed("test -e /var/lib/conflict-" + node.name + "/INCOMPLETE; test ! -e /var/lib/conflict-" + node.name + "/stage.json")
        storage.succeed("${pkgs.shadow}/bin/userdel restore-collision")
        restored = next(row["staged"] for row in staged["paths"] if row["source"] == data)
        inspect = "import os,pathlib,struct; p=pathlib.Path(" + repr(restored) + "); assert (p/'example').read_text()=='only-encrypted-application-data'; assert (p/'example').stat().st_ino==(p/'hardlink').stat().st_ino; assert os.readlink(p/'symlink')=='example'; assert os.getxattr(p/'example','user.nixberry-test')==b'preserved'; acl=os.getxattr(p/'example','system.posix_acl_access'); assert struct.pack('<HHI',2,4,12345) in acl; assert (p/'sparse').stat().st_size==8*1024*1024+3; assert (p/'sparse').stat().st_blocks*512 < (p/'sparse').stat().st_size//2; assert (p/'example').stat().st_uid==0"
        storage.succeed("${pkgs.python3}/bin/python3 -c " + shlex.quote(inspect))
        if node == btrfs:
            assert not storage.succeed("find " + shlex.quote(restored) + " -name hidden -print").strip()
            restored_child = next(row["staged"] for row in staged["paths"] if row["source"] == nested)
            storage.succeed("grep -Fx live-child " + shlex.quote(restored_child + "/current"))
        storage.succeed("test ! -e /var/lib/restored-" + node.name + "/INCOMPLETE; test ! -e /var/lib/nixberry-restore")
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
