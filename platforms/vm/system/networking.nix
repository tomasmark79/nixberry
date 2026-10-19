{ config, lib, ... }:
let
  network = config.nixberry.vm.network;
  static = network.mode == "static";
in
{
  options.nixberry.vm.network = lib.mkOption {
    description = "Síť konkrétní VM nezávislá na způsobu doručení obrazu.";
    default = { };
    type = lib.types.submodule {
      options = {
        mode = lib.mkOption {
          type = lib.types.enum [
            "dhcp"
            "static"
          ];
          default = "dhcp";
          description = "Automatická nebo pevná IPv4 adresa.";
        };
        address = lib.mkOption {
          type = lib.types.str;
          default = "";
          description = "Pevná IPv4 adresa včetně prefixu, například 10.0.1.19/24.";
        };
        gateway = lib.mkOption {
          type = lib.types.str;
          default = "";
          description = "Výchozí IPv4 brána.";
        };
        dns = lib.mkOption {
          type = lib.types.listOf lib.types.str;
          default = [ ];
          description = "Adresy DNS serverů pro pevnou konfiguraci.";
        };
      };
    };
  };

  config = {
    assertions = [
      {
        assertion = !static || (network.address != "" && network.gateway != "" && network.dns != [ ]);
        message = "Pevná síť VM vyžaduje address s prefixem, gateway a neprázdné dns.";
      }
    ];
    networking.useDHCP = false;
    networking.useNetworkd = true;
    networking.nameservers = lib.mkIf static network.dns;
    systemd.network = {
      enable = true;
      networks."10-lan" = {
        # Jedna síťová karta; MAC volí provozovatel při vytvoření VM.
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
