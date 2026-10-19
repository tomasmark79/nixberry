{ config, lib, ... }:
let
  network = config.nixberry.vm.network;
  static = network.mode == "static";
in
{
  options.nixberry.vm.network = lib.mkOption {
    description = "Per-VM networking independent of image delivery.";
    default = { };
    type = lib.types.submodule {
      options = {
        mode = lib.mkOption {
          type = lib.types.enum [
            "dhcp"
            "static"
          ];
          default = "dhcp";
          description = "Automatic or static IPv4 address.";
        };
        address = lib.mkOption {
          type = lib.types.str;
          default = "";
          description = "Static IPv4 address including prefix, for example 10.0.1.19/24.";
        };
        gateway = lib.mkOption {
          type = lib.types.str;
          default = "";
          description = "Default IPv4 gateway.";
        };
        dns = lib.mkOption {
          type = lib.types.listOf lib.types.str;
          default = [ ];
          description = "DNS server addresses for static configuration.";
        };
      };
    };
  };

  config = {
    assertions = [
      {
        assertion = !static || (network.address != "" && network.gateway != "" && network.dns != [ ]);
        message = "Static VM networking requires address with prefix, gateway and nonempty dns.";
      }
    ];
    networking.useDHCP = false;
    networking.useNetworkd = true;
    networking.nameservers = lib.mkIf static network.dns;
    systemd.network = {
      enable = true;
      networks."10-lan" = {
        # One network adapter; the operator selects its MAC during VM creation.
        matchConfig.Name = "en* eth*";
        networkConfig = {
          DHCP = if static then "no" else "ipv4";
          IPv6AcceptRA = false;
        }
        // lib.optionalAttrs static {
          Address = network.address;
          Gateway = network.gateway;
          DNS = network.dns;
        };
      };
    };
  };
}
