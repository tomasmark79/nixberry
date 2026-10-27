{ config, lib, ... }:
let
  network = config.nixberry.vm.network;
  static = network.mode == "static";
  addresses = import ../../../modules/system/networking/addresses.nix { inherit lib; };
  adapters = config.nixberry.vm.adapters;
  primary = if adapters == [ ] then null else builtins.head adapters;
  secondary = if adapters == [ ] then [ ] else builtins.tail adapters;
  extra = network.interfaces;
  addressOptions = {
    mode = lib.mkOption {
      type = lib.types.enum [
        "disabled"
        "dhcp"
        "static"
      ];
      default = "disabled";
    };
    address = lib.mkOption {
      type = lib.types.str;
      default = "";
    };
    gateway = lib.mkOption {
      type = lib.types.str;
      default = "";
    };
    dns = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
    };
  };
in
{
  imports = [ ../../../modules/system/networking/facts.nix ];
  options.nixberry.vm.adapters = lib.mkOption {
    description = "Ordered target NIC identities; the first adapter provides management access.";
    default = [ ];
    type = lib.types.listOf (
      lib.types.submodule {
        options = {
          name = lib.mkOption { type = lib.types.strMatching "[a-z][a-z0-9-]{0,14}"; };
          mac = lib.mkOption { type = lib.types.strMatching "([0-9a-f]{2}:){5}[0-9a-f]{2}"; };
        };
      }
    );
  };
  options.nixberry.vm.network = lib.mkOption {
    description = "Per-VM networking independent of image delivery.";
    default = { };
    type = lib.types.submodule {
      options = {
        interfaces = lib.mkOption {
          type = lib.types.attrsOf (lib.types.submodule { options = addressOptions; });
          default = { };
          description = "Addressing for secondary named adapters; omitted adapters have no DHCP, addresses or RA.";
        };
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
          description = "DNS server IPv4 or IPv6 addresses for static configuration.";
        };
      };
    };
  };

  config = {
    nixberry.network = {
      primaryInterface = if primary == null then null else primary.name;
      networkdUnits = builtins.listToAttrs (
        map (adapter: {
          name = adapter.name;
          value = if adapter == primary then "10-lan" else "20-nixberry-${adapter.name}";
        }) adapters
      );
      interfaces = builtins.listToAttrs (
        map (adapter: {
          name = adapter.name;
          value = {
            inherit (adapter) name mac;
          }
          // (
            if adapter == primary then
              {
                inherit (network)
                  mode
                  address
                  gateway
                  dns
                  ;
              }
            else
              extra.${adapter.name} or {
                mode = "disabled";
                address = "";
                gateway = "";
                dns = [ ];
              }
          );
        }) adapters
      );
    };
    assertions = [
      {
        assertion = builtins.all (name: builtins.elem name (map (adapter: adapter.name) secondary)) (
          builtins.attrNames extra
        );
        message = "network.interfaces may only configure declared secondary adapter names.";
      }
      {
        assertion = builtins.all (
          value:
          (value.mode != "static" || addresses.ipv4WithPrefix value.address)
          && (value.gateway == "" || (value.mode == "static" && addresses.ipv4 value.gateway))
          && builtins.all addresses.ip value.dns
        ) (builtins.attrValues extra);
        message = "Secondary static interfaces require a valid IPv4 prefix and valid optional gateway and DNS addresses.";
      }
      {
        assertion = !static || (network.address != "" && network.gateway != "" && network.dns != [ ]);
        message = "Static VM networking requires address with prefix, gateway and nonempty dns.";
      }
      {
        assertion = !static || addresses.ipv4WithPrefix network.address;
        message = "Static VM networking requires a valid IPv4 address with a prefix length from 0 to 32.";
      }
      {
        assertion = !static || addresses.ipv4 network.gateway;
        message = "Static VM networking requires a valid IPv4 gateway address.";
      }
      {
        assertion = !static || builtins.all addresses.ip network.dns;
        message = "Static VM DNS servers must be valid IPv4 or IPv6 addresses.";
      }
    ];
    networking.useDHCP = false;
    networking.useNetworkd = true;
    networking.nameservers = lib.mkIf static network.dns;
    systemd.network = {
      enable = true;
      links = builtins.listToAttrs (
        map (adapter: {
          name = "10-nixberry-${adapter.name}";
          value = {
            matchConfig.MACAddress = adapter.mac;
            linkConfig.Name = adapter.name;
          };
        }) adapters
      );
      networks = {
        "10-lan" = {
          matchConfig = if primary == null then { Name = "en* eth*"; } else { MACAddress = primary.mac; };
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
      }
      // builtins.listToAttrs (
        map (adapter: {
          name = "20-nixberry-${adapter.name}";
          value =
            let
              value =
                extra.${adapter.name} or {
                  mode = "disabled";
                  address = "";
                  gateway = "";
                  dns = [ ];
                };
            in
            {
              matchConfig.MACAddress = adapter.mac;
              linkConfig.RequiredForOnline = "no";
              networkConfig = {
                DHCP = if value.mode == "dhcp" then "ipv4" else "no";
                IPv6AcceptRA = false;
                LinkLocalAddressing = "no";
              }
              // lib.optionalAttrs (value.mode == "static") {
                Address = value.address;
              }
              // lib.optionalAttrs (value.gateway != "") {
                Gateway = value.gateway;
              }
              // lib.optionalAttrs (value.dns != [ ]) { DNS = value.dns; };
            };
        }) secondary
      );
    };
  };
}
