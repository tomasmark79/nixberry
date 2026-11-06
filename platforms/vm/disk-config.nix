{ config, lib, ... }:
{
  options.nixberry.vm.rootFilesystem = lib.mkOption {
    type = lib.types.enum [
      "ext4"
      "btrfs"
    ];
    default = "ext4";
    description = "Declared root filesystem; changing it requires a separately authorized installation.";
  };
  config = {
    # The hypervisor profile selects the virtual disk; installation erases its content.
    disko.devices.disk.system = {
      type = "disk";
      device = lib.mkDefault "/dev/vda";
      content = {
        type = "gpt";
        partitions = {
          boot = {
            size = "512M";
            type = "EF00";
            content = {
              type = "filesystem";
              format = "vfat";
              mountpoint = "/boot";
              mountOptions = [ "umask=0077" ];
            };
          };
          root = {
            size = "100%";
            content = import ../storage/root-content.nix {
              filesystem = config.nixberry.vm.rootFilesystem;
            };
          };
        };
      };
    };
  };
}
