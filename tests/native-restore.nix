# Full global restoration with source loss and a reproducible selected recipe.
{ pkgs }:
let
  lib = pkgs.lib;
  tools = import ../management/deployment/capture-package.nix { inherit pkgs; };
  controller = import ../management/deployment/package.nix { inherit pkgs; };
  originalLock = builtins.fromJSON (builtins.readFile ../flake.lock);
  pinned =
    originalLock.nodes.${
      if originalLock.nodes ? nixos-raspberrypi then
        originalLock.nodes.nixos-raspberrypi.inputs.nixpkgs
      else
        "nixpkgs"
    };
  fixtureLock = pkgs.writeText "restore-fixture.lock" (
    builtins.toJSON {
      version = 7;
      root = "root";
      nodes = {
        root.inputs.nixpkgs = "nixpkgs";
        nixpkgs = pinned;
      };
    }
  );
  fixtureFlake = pkgs.writeText "restore-fixture-flake.nix" ''
    {
      inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
      outputs = { nixpkgs, ... }: let
        pkgs = nixpkgs.legacyPackages.x86_64-linux;
        test = import ./tests/native-restore.nix { inherit pkgs; };
      in {
        nixberrySystems = {
          vm-demo-source.config = test.config.nodes.source;
          vm-demo-destination.config = test.config.nodes.destination;
        };
        nixberryDeployment = { targets = import ./machines/vm; hosts = {}; };
      };
    }
  '';
  fixtureSource = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../management
      ../modules
      ../platforms
      ../tests
      ../VERSION
      ../hosts.nix
    ];
  };
  server = pkgs.writeText "restore-demo-server.py" ''
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import sqlite3
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            with sqlite3.connect('file:/var/lib/restore-demo/state.sqlite?mode=ro', uri=True) as connection:
                value = connection.execute('select value from state').fetchone()[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(value.encode())
    HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
  '';
  common = prepare: { ... }: {
    imports = [ ../modules/services/backup ];
    virtualisation = {
      memorySize = 1536;
      diskSize = 8192;
      useBootLoader = true;
      useEFIBoot = true;
    };
    boot.loader.grub.efiSupport = true;
    boot.loader.grub.efiInstallAsRemovable = true;
    services.openssh.enable = true;
    services.openssh.settings.PermitRootLogin = "prohibit-password";
    nixberry.backup = {
      enable = true;
      captureMethod = "cold";
      restore.prepare = prepare;
      applications.demo = {
        paths = [ "/var/lib/restore-demo" ];
        stopUnits = [ "demo.service" ];
      };
    };
    users.groups.demo.gid = 2001;
    users.users.demo = {
      isSystemUser = true;
      uid = 2001;
      group = "demo";
    };
    systemd.tmpfiles.rules = [ "d /var/lib/restore-demo 0750 demo demo -" ];
    systemd.services.demo = {
      wantedBy = [ "multi-user.target" ];
      serviceConfig.User = "demo";
      script = "exec ${pkgs.python3}/bin/python3 ${server}";
    };
    environment.systemPackages = [
      pkgs.gptfdisk
      pkgs.openssh
      pkgs.curl
      pkgs.python3
    ];
  };
  declaration = name: {
    backend = "libvirt";
    offline = true;
    configuration = "default";
    access = {
      host = name;
      knownHostsFile = "/root/.ssh/known_hosts";
    };
    spec = {
      inherit name;
      uuid = "11111111-1111-4111-8111-111111111111";
    };
  };
  registry = pkgs.writeText "restore-fixture-registry.json" (
    builtins.toJSON {
      vm-demo-source = declaration "source";
      vm-demo-destination = declaration "destination";
    }
  );
in
pkgs.testers.runNixOSTest {
  name = "nixberry-native-restore";
  nodes = {
    source = { ... }: {
      imports = [ (common false) ];
      nixberry.backup.restic = {
        enable = true;
        repository = "sftp:root@storage:/backup/source";
        ssh = {
          port = 22;
          identityFile = "/var/lib/nixberry-backup/ssh-key";
          knownHostsFile = "/var/lib/nixberry-backup/known_hosts";
        };
      };
    };
    destination = common true;
    storage = { ... }: {
      virtualisation.memorySize = 4096;
      services.openssh.enable = true;
      services.openssh.settings.PermitRootLogin = "prohibit-password";
      nix.settings.experimental-features = [
        "nix-command"
        "flakes"
      ];
      environment.etc."restore-fixture-nixpkgs".source = builtins.path {
        path = pkgs.path;
        name = "source";
      };
      environment.systemPackages = [
        controller
        pkgs.restic
        pkgs.nix
        pkgs.python3
        pkgs.openssh
        pkgs.curl
      ];
    };
  };
  testScript = ''
    import json, shlex
    source.start()
    destination.start(allow_reboot=True)
    storage.start()
    for node in [source, destination, storage]:
        node.wait_for_unit("multi-user.target")
        node.succeed("install -d -m 700 /root/.ssh")
    for node in [source, destination]:
        node.succeed("test -d /sys/firmware/efi; disk=/dev/$(lsblk -ndo PKNAME $(findmnt -n --nofsroot -o SOURCE /)); sgdisk --change-name=1:disk-system-boot --change-name=2:disk-system-root $disk; udevadm trigger --subsystem-match=block; udevadm settle")
    storage.succeed("ssh-keygen -q -t ed25519 -N \"\" -f /root/.ssh/id_ed25519; ssh-keygen -q -t ed25519 -N \"\" -f /root/readonly-key; mkdir /backup")
    public = storage.succeed("cat /root/.ssh/id_ed25519.pub").strip()
    for node in [source, destination]:
        node.succeed("printf '%s\\n' " + shlex.quote(public) + " > /root/.ssh/authorized_keys; chmod 600 /root/.ssh/authorized_keys")
        host = node.succeed("cat /etc/ssh/ssh_host_ed25519_key.pub").strip()
        storage.succeed("printf '%s\\n' " + shlex.quote(node.name + " " + host) + " >> /root/.ssh/known_hosts")
    source.succeed("install -d -m 700 /var/lib/nixberry-backup; printf test-only-password > /var/lib/nixberry-backup/password; chmod 600 /var/lib/nixberry-backup/password; ssh-keygen -q -t ed25519 -N \"\" -f /var/lib/nixberry-backup/ssh-key")
    source_public = source.succeed("cat /var/lib/nixberry-backup/ssh-key.pub").strip()
    readonly = storage.succeed("cat /root/readonly-key.pub").strip()
    storage.succeed("printf '%s\\n' " + shlex.quote(source_public) + " " + shlex.quote('restrict,command="internal-sftp -R" ' + readonly) + " > /root/.ssh/authorized_keys; chmod 600 /root/.ssh/authorized_keys")
    storage_host = storage.succeed("cat /etc/ssh/ssh_host_ed25519_key.pub").strip()
    source.succeed("printf '%s\\n' " + shlex.quote("storage " + storage_host) + " > /var/lib/nixberry-backup/known_hosts")
    storage.succeed("printf '%s\\n' " + shlex.quote("storage " + storage_host) + " >> /root/.ssh/known_hosts; chmod 600 /root/.ssh/known_hosts")
    storage.succeed("mkdir /root/recipe; cp -r ${fixtureSource}/. /root/recipe/; chmod -R u+w /root/recipe; cp ${fixtureFlake} /root/recipe/flake.nix; cp ${fixtureLock} /root/recipe/flake.lock; mkdir -p /root/recipe/machines/vm /root/recipe/machines/rpi /root/recipe/configurations; cp /root/.ssh/id_ed25519.pub /root/recipe/admin.pub")
    declarations = json.loads(storage.succeed("cat ${registry}"))
    for name, value in declarations.items():
        encoded = json.dumps(json.dumps(value)).replace('$' + '{', '\\$' + '{')
        storage.succeed("printf '%s\\n' " + shlex.quote("builtins.fromJSON " + encoded) + " > /root/recipe/machines/vm/" + name + ".nix")
    storage.succeed("printf '%s\\n' 'let f=builtins.readDir ./.; n=builtins.filter (x: x != \"default.nix\" && builtins.match \".*\\\\.nix\" x != null) (builtins.attrNames f); in builtins.listToAttrs (map (x: {name=builtins.substring 0 (builtins.stringLength x - 4) x; value=import (./. + (\"/\" + x));}) n)' > /root/recipe/machines/vm/default.nix; printf '{}\\n' > /root/recipe/machines/rpi/default.nix; printf '{}\\n' > /root/recipe/configurations/default.nix; cp ${registry} /root/registry.json")
    command = "env NIXBERRY_LANG=en NIXBERRY_RECIPE=/root/recipe NIXBERRY_DEPLOYMENT=/root/registry.json PYTHONPATH=${controller.source} ${pkgs.python3}/bin/python3 ${controller.source}/cli.py "
    initialize = "import sqlite3,os; p='/var/lib/restore-demo/'; c=sqlite3.connect(p+'state.sqlite'); c.execute('create table state(value text)'); c.execute(\"insert into state values ('restored hello')\"); c.commit(); c.close(); os.chown(p+'state.sqlite',2001,2001); os.link(p+'state.sqlite',p+'hardlink'); f=open(p+'sparse','wb'); f.seek(8*1024**3); f.write(b'end'); f.close(); os.chown(p+'sparse',2001,2001); os.symlink('state.sqlite',p+'symlink'); os.lchown(p+'symlink',2001,2001); os.mkdir(p+'nested'); open(p+'nested/example','w').write('nested data')"
    source.succeed("${pkgs.python3}/bin/python3 -c " + shlex.quote(initialize))
    source.wait_until_succeeds("curl -fsS localhost:8080 | grep -Fx 'restored hello'")
    source.succeed("nixberry-restic backup-init --apply --json")
    storage.succeed(command + "backup-stage vm-demo-source --apply --json")
    captured = json.loads(storage.succeed(command + "backup vm-demo-source --apply --json"))
    source_system = source.succeed("readlink -f /run/current-system").strip()
    destination_machine = destination.succeed("cat /etc/machine-id").strip()
    destination_ssh = destination.succeed("sha256sum /etc/ssh/ssh_host_ed25519_key").split()[0]
    policy = json.loads(source.succeed("cat /etc/nixberry/backup.json"))
    pin = source.succeed("cat /var/lib/nixberry-backup/repository-id").strip()
    access = {"version":1,"target":"vm-demo-source","sourceId":policy["sourceId"],"repository":policy["repository"],"repositoryId":pin,"passwordFile":"/root/restore-password","runtimeDirectory":"/run/restore-controller","timeoutSeconds":600,"ssh":{"port":22,"identityFile":"/root/readonly-key","knownHostsFile":"/root/.ssh/known_hosts"}}
    storage.succeed("printf '%s' " + shlex.quote(json.dumps(access)) + " > /root/access.json; printf test-only-password > /root/restore-password; chmod 600 /root/access.json /root/restore-password")
    source.shutdown()
    storage.succeed("rm /root/recipe/machines/vm/vm-demo-source.nix")
    declarations.pop("vm-demo-source")
    storage.succeed("printf '%s' " + shlex.quote(json.dumps(declarations)) + " > /root/registry.json")
    operation = command + "restore vm-demo-destination --from vm-demo-source --backup-access /root/access.json --snapshot " + captured["snapshotId"] + " --json"
    assert json.loads(storage.succeed(operation))["applied"] is False
    destination.succeed("test ! -e /var/lib/restore-demo/state.sqlite")
    storage.execute(operation + " --apply > /tmp/restore-output 2>&1 &")
    interrupt = "import pathlib,json,os,signal; p=pathlib.Path('/var/lib/nixberry-restore/restore-pending.json'); assert p.exists() and json.loads(p.read_text())['phase']=='copying'; procs=[x for x in pathlib.Path('/proc').iterdir() if x.name.isdigit()]; ids=[int(x.name) for x in procs if any(a.endswith(b'/restore_target.py') for a in (x/'cmdline').read_bytes().split(bytes([0])))]; assert len(ids)==1; os.kill(ids[0],signal.SIGKILL)"
    destination.wait_until_succeeds("${pkgs.python3}/bin/python3 -c " + shlex.quote(interrupt), timeout=300)
    destination.fail("systemctl is-active demo.service")
    destination.succeed("test -e /var/lib/nixberry-restore/restore-pending.json; test -z \"$(find /var/lib/nixberry-restore -name 'completed-*' -print)\"")
    destination.reboot()
    destination.wait_for_unit("multi-user.target")
    destination.fail("systemctl is-active demo.service")
    restored = json.loads(storage.succeed(operation + " --apply"))
    assert restored["state"] == "recorded-restored"
    destination.wait_until_succeeds("curl -fsS localhost:8080 | grep -Fx 'restored hello'")
    assert destination.succeed("cat /etc/machine-id").strip() == destination_machine
    assert destination.succeed("sha256sum /etc/ssh/ssh_host_ed25519_key").split()[0] == destination_ssh
    destination.succeed("test ! -e /var/lib/nixberry-restore/restore-pending.json; test -z \"$(find /run/nixberry-restore-access -name access.json -print)\"")
    recovered = command + "recover-source vm-demo-source --backup-access /root/access.json --snapshot " + captured["snapshotId"] + " --output /root/recovered --apply --json"
    storage.succeed(recovered)
    rebuilt = storage.succeed("nix eval --raw --no-update-lock-file path:/root/recovered/recipe#nixberrySystems.vm-demo-source.config.system.build.toplevel.outPath").strip()
    assert rebuilt == source_system, (rebuilt, source_system)
  '';
}
