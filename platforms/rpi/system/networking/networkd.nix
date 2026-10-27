{ config, lib, ... }@arguments:
let
  cfg = config.nixberry.rpi;
  interfaceName = mac: "enx${lib.replaceStrings [ ":" ] [ "" ] mac}";
  named = cfg.network != null;
  recovery = named && (arguments.nixberryRpiRecovery or false);
  rows =
    if named then
      builtins.mapAttrs (
        name: value:
        if !recovery then
          value
        else
          value
          // {
            mode = if name == cfg.network.recoveryInterface then "dhcp" else "disabled";
            address = "";
            gateway = "";
            dns = [ ];
          }
      ) cfg.network.interfaces
    else
      {
        ${interfaceName cfg.lanMac} = {
          mac = cfg.lanMac;
          mode = "static";
          address = cfg.address;
          gateway = cfg.gateway;
          dns = cfg.dns;
        };
      }
      // lib.optionalAttrs (cfg.reserveMac != null) {
        ${interfaceName cfg.reserveMac} = {
          mac = cfg.reserveMac;
          mode = "disabled";
          address = "";
          gateway = "";
          dns = [ ];
        };
      };
in
{
  imports = [ ../../../../modules/system/networking/facts.nix ];
  nixberry.network = {
    primaryInterface =
      if recovery then
        cfg.network.recoveryInterface
      else if named then
        cfg.network.primary
      else
        interfaceName cfg.lanMac;
    networkdUnits = builtins.mapAttrs (
      name: _:
      if named then
        "10-nixberry-${name}"
      else if name == interfaceName cfg.lanMac then
        "10-lan"
      else
        "20-reserve"
    ) rows;
    interfaces = builtins.mapAttrs (name: value: value // { inherit name; }) rows;
  };
  networking.useDHCP = false;
  networking.useNetworkd = true;
  networking.nameservers = lib.optionals (named && !recovery) cfg.dns;
  # Names follow MAC addresses and do not depend on adapter attachment order.
  systemd.network =
    if named then
      {
        enable = true;
        links = lib.mapAttrs' (
          name: value:
          lib.nameValuePair "10-nixberry-${name}" {
            matchConfig.MACAddress = value.mac;
            linkConfig.Name = name;
          }
        ) rows;
        networks = lib.mapAttrs' (
          name: value:
          lib.nameValuePair "10-nixberry-${name}" {
            matchConfig.MACAddress = value.mac;
            address = lib.optional (value.mode == "static") value.address;
            gateway = lib.optional (value.gateway != "") value.gateway;
            dns = value.dns;
            linkConfig.RequiredForOnline =
              if
                value.mode != "disabled"
                && (
                  name == (if recovery then cfg.network.recoveryInterface else cfg.network.primary)
                  || value.gateway != ""
                )
              then
                "yes"
              else
                "no";
            networkConfig = {
              DHCP = if value.mode == "dhcp" then "ipv4" else "no";
              IPv6AcceptRA = false;
              LinkLocalAddressing = "no";
            }
            // lib.optionalAttrs (value.mode == "static" && value.gateway == "") {
              ConfigureWithoutCarrier = true;
            };
          }
        ) rows;
      }
    else
      {
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
