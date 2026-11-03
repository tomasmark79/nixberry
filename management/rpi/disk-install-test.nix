# Run actual ARM installation on a disposable SSD inside an x86 Linux VM.
{
  pkgs,
  installer,
  target,
}:
pkgs.testers.runNixOSTest {
  name = "rpi-local-ssd";
  nodes.workstation = { ... }: {
    boot.binfmt.emulatedSystems = [ "aarch64-linux" ];
    environment.systemPackages = [
      installer
      pkgs.nixos-install-tools
      pkgs.e2fsprogs
    ];
    virtualisation.memorySize = 4096;
    virtualisation.cores = 4;
    virtualisation.diskSize = 2048;
    virtualisation.emptyDiskImages = [
      {
        size = 12288;
        driveConfig.deviceExtraOpts.serial = target.diskSerial;
      }
    ];
  };
  testScript = ''
    start_all()
    workstation.wait_for_unit("multi-user.target")
    disk = "/dev/disk/by-id/virtio-${target.diskSerial}"
    command = "nixberry-rpi-install-disk --local-disk " + disk
    workstation.succeed("test -b " + disk)
    # Default planning must leave the entire blank disk unchanged.
    before = workstation.succeed("sha256sum " + disk).split()[0]
    workstation.succeed(command + " --dry-run")
    assert workstation.succeed("sha256sum " + disk).split()[0] == before
    workstation.succeed("echo 0 > /proc/sys/fs/binfmt_misc/aarch64-linux")
    workstation.fail(command + " --apply")
    assert workstation.succeed("sha256sum " + disk).split()[0] == before
    workstation.succeed("echo 1 > /proc/sys/fs/binfmt_misc/aarch64-linux")
    workstation.succeed(command + " --apply", timeout=1200)
    workstation.succeed("test -z \"$(lsblk -nr -o MOUNTPOINTS " + disk + " | tr -d '[:space:]')\"")
    workstation.succeed("mkdir -p /target; mount /dev/disk/by-partlabel/${target.rootLabel} /target")
    workstation.succeed("mount /dev/disk/by-partlabel/${target.bootLabel} /target/boot")
    workstation.succeed("test -e /target/etc/NIXOS; test -s /target/boot/config.txt")
    workstation.succeed("nix-store --store /target --verify --check-contents", timeout=600)
    workstation.succeed("test \"$(readlink -f /target/nix/var/nix/profiles/system)\" = '${target.systemPath}'")
    workstation.succeed("find /target/boot/rpi-generations -name kernel.img -size +0 | grep .")
    workstation.succeed("grep -R -F -x --include=system-link '${target.systemPath}' /target/boot/rpi-generations")
    workstation.succeed("grep -R -F --include=cmdline.txt 'init=' /target/boot/rpi-generations")
    workstation.fail(command + " --apply --erase-existing")
    workstation.succeed("umount /target/boot /target")
    # An existing GPT is never silently reused or erased.
    workstation.fail(command + " --apply")
    workstation.succeed(command + " --dry-run --erase-existing")
    workstation.succeed(command + " --apply --erase-existing", timeout=1200)
    workstation.succeed("test -z \"$(lsblk -nr -o MOUNTPOINTS " + disk + " | tr -d '[:space:]')\"")
    # Host installation, boot filesystem and services remain the workstation's.
    workstation.succeed("test $(uname -m) = x86_64; test $(hostname) = workstation")
    workstation.succeed("systemctl is-active multi-user.target")
  '';
}
