# RPi parameters are separate from shared services and VM configuration.
{ config, lib, ... }:
let
  cfg = config.nixberry.rpi;
  inherit (lib) mkOption types;
  mac = types.strMatching "([0-9a-f]{2}:){5}[0-9a-f]{2}";
in
{
  options.nixberry.rpi = {
    hostName = mkOption {
      type = types.strMatching "[a-zA-Z0-9][a-zA-Z0-9-]*";
      description = "Target RPi hostname.";
    };
    hardwareSerial = mkOption {
      type = types.strMatching "[0-9a-f]{16}";
      description = "RPi serial number from the device tree.";
    };
    installerAddress = mkOption {
      type = types.str;
      description = "Reachable IPv4 address of the RPi or installation SD.";
    };
    address = mkOption {
      type = types.str;
      description = "Persistent system address including prefix length.";
    };
    gateway = mkOption {
      type = types.str;
      description = "Target system default gateway.";
    };
    dns = mkOption {
      type = types.listOf types.str;
      description = "Target system DNS servers.";
    };
    lanMac = mkOption {
      type = mac;
      description = "Primary network adapter MAC address.";
    };
    reserveMac = mkOption {
      type = types.nullOr mac;
      default = null;
      description = "Optional spare adapter MAC address.";
    };
    diskDevice = mkOption {
      type = types.strMatching "/dev/disk/by-id/[^/]+";
      description = "Stable by-id path of the target disk.";
    };
    diskSerial = mkOption {
      type = types.str;
      description = "Target disk serial reported by lsblk.";
    };
    rootGiB = mkOption {
      type = types.ints.positive;
      default = 256;
      description = "System partition size in GiB.";
    };
    bootLabel = mkOption {
      type = types.strMatching "[A-Za-z0-9-]{1,36}";
      default = "${cfg.hostName}-boot";
      description = "Boot partition GPT label.";
    };
    rootLabel = mkOption {
      type = types.strMatching "[A-Za-z0-9-]{1,16}";
      default = "${cfg.hostName}-root";
      description = "System partition and ext4 label.";
    };
    adminKeyFile = mkOption {
      type = types.path;
      description = "Administrator public key file.";
    };
  };
  config.assertions = [
    {
      assertion = cfg.reserveMac == null || cfg.reserveMac != cfg.lanMac;
      message = "Primary and spare RPi adapters must have different MAC addresses.";
    }
    {
      assertion = cfg.installerAddress != "192.168.79.1";
      message = "The protected router must not be an installation target.";
    }
    {
      assertion = cfg.bootLabel != cfg.rootLabel;
      message = "RPi partition labels must differ.";
    }
  ];
}
