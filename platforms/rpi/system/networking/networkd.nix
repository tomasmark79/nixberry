{ config, lib, ... }:
let
  cfg = config.nixberry.rpi;
  interfaceName = mac: "enx${lib.replaceStrings [ ":" ] [ "" ] mac}";
in
{
  networking.useDHCP = false;
  networking.useNetworkd = true;
  # Names follow MAC addresses and do not depend on adapter attachment order.
  systemd.network = {
    enable = true;
    links = {
      "10-lan" = {
        matchConfig.MACAddress = cfg.lanMac;
        linkConfig.Name = interfaceName cfg.lanMac;
      };
    }
    // lib.optionalAttrs (cfg.reserveMac != null) {
      "10-reserve" = {
        matchConfig.MACAddress = cfg.reserveMac;
        linkConfig.Name = interfaceName cfg.reserveMac;
      };
    };
    networks = {
      "10-lan" = {
        matchConfig.MACAddress = cfg.lanMac;
        address = [ cfg.address ];
        gateway = [ cfg.gateway ];
        dns = cfg.dns;
        networkConfig = {
          DHCP = "no";
          IPv6AcceptRA = false;
        };
      };
    }
    // lib.optionalAttrs (cfg.reserveMac != null) {
      # The spare adapter has no IP or gateway; do not wait for its cable.
      "20-reserve" = {
        matchConfig.MACAddress = cfg.reserveMac;
        linkConfig.RequiredForOnline = "no";
        networkConfig = {
          DHCP = "no";
          LinkLocalAddressing = "no";
          IPv6AcceptRA = false;
        };
      };
    };
  };
}
