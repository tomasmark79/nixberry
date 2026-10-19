{ config, ... }:
let
  cfg = config.nixberry.rpi;
in
{
  # This layout erases only the authorized SSD; the SD card is not included.
  disko.devices.disk.ssd = {
    type = "disk";
    device = cfg.diskDevice;
    content = {
      type = "gpt";
      partitions = {
        boot = {
          priority = 1;
          size = "512M";
          type = "EF00";
          label = cfg.bootLabel;
          # This flag lets U-Boot select the partition containing boot files.
          attributes = [ 2 ];
          content = {
            type = "filesystem";
            format = "vfat";
            extraArgs = [
              "-F"
              "32"
              "-n"
              "NIXBOOT"
            ];
            mountpoint = "/boot";
            mountOptions = [ "umask=0077" ];
          };
        };
        root = {
          size = "${toString cfg.rootGiB}G";
          label = cfg.rootLabel;
          content = {
            type = "filesystem";
            format = "ext4";
            extraArgs = [
              "-L"
              cfg.rootLabel
            ];
            mountpoint = "/";
            mountOptions = [ "noatime" ];
          };
        };
      };
    };
  };
}
