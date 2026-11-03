# Host-native disk tools, selected ARM closure, no recovery image or SSH access.
{
  pkgs,
  target,
  system,
  disko,
}:
let
  commonSource = import ../messages.nix { inherit pkgs; };
  declaredLayout = import ../../platforms/rpi/disk-config.nix {
    config.nixberry.rpi = system.config.nixberry.rpi;
  };
  actual = system.config.disko.devices.disk.ssd;
  expected = declaredLayout.disko.devices.disk.ssd;
  partitionMatches =
    name:
    let
      a = actual.content.partitions.${name};
      e = {
        type = "8300";
        priority = 1000;
        attributes = [ ];
      }
      // expected.content.partitions.${name};
    in
    pkgs.lib.all (key: a.${key} == e.${key}) [
      "size"
      "label"
      "type"
      "priority"
      "attributes"
    ]
    && pkgs.lib.all (key: a.content.${key} == e.content.${key}) [
      "type"
      "format"
      "extraArgs"
      "mountpoint"
      "mountOptions"
    ];
  supportedLayout =
    builtins.attrNames system.config.disko.devices.disk == [ "ssd" ]
    && actual.content.type == "gpt"
    &&
      builtins.attrNames actual.content.partitions == [
        "boot"
        "root"
      ]
    && partitionMatches "boot"
    && partitionMatches "root";
  layout = {
    disko.devices.disk.ssd = declaredLayout.disko.devices.disk.ssd // {
      device = "/mnt/disk";
      content = declaredLayout.disko.devices.disk.ssd.content // {
        partitions = declaredLayout.disko.devices.disk.ssd.content.partitions // {
          boot = declaredLayout.disko.devices.disk.ssd.content.partitions.boot // {
            device = "/mnt/part1";
          };
          root = declaredLayout.disko.devices.disk.ssd.content.partitions.root // {
            device = "/mnt/part2";
          };
        };
      };
    };
  };
  nativeDisko =
    (import disko {
      inherit (pkgs) lib;
      rootMountPoint = "/mnt/root";
    })._cliFormatMount
      layout
      pkgs;
  manifest = pkgs.writeText "nixberry-rpi-local-disk.json" (
    builtins.toJSON (
      target
      // {
        localDiskoPath = "${nativeDisko}/bin/disko-format-mount";
      }
    )
  );
in
assert
  supportedLayout
  || throw "Direct RPi SSD installation requires the declared standard boot/root layout; use recovery media for a custom layout.";
pkgs.writeShellApplication {
  name = "nixberry-rpi-install-disk";
  runtimeInputs = with pkgs; [
    python3
    nix
    util-linux
    coreutils
    systemd
    nixos-install-tools
  ];
  text = ''
    export NIXBERRY_RPI_DISK_MANIFEST=${manifest}
    export PYTHONPATH=${commonSource}
    exec python3 ${./disk_install.py} "$@"
  '';
}
