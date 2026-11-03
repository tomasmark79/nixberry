# NIC identities and addresses come from the target; services only consume them.
{
  config,
  lib,
  pkgs,
  ...
}:
let
  interfaces = config.nixberry.network.interfaces;
  lan =
    interfaces.lan0 or {
      address = "";
      mode = "disabled";
      gateway = "";
    };
  wan =
    interfaces.wan0 or {
      address = "";
      mode = "disabled";
      gateway = "";
      dns = [ ];
    };
  lanIP = builtins.head (lib.splitString "/" lan.address);
  octets = lib.splitString "." lanIP;
  prefix = lib.concatStringsSep "." (lib.take 3 octets);
  lanSubnet = "${prefix}.0/24";
  addresses = import ../system/networking/addresses.nix { };
  wanIP = builtins.head (lib.splitString "/" wan.address);
  wanPrefix = lib.last (lib.splitString "/" wan.address);
  overlaps =
    addresses.sameIPv4Subnet "${lanIP}/${wanPrefix}" wan.address
    || addresses.sameIPv4Subnet "${wanIP}/24" lan.address;
in
{
  assertions = [
    {
      assertion =
        lan.mode == "static"
        && wan.mode == "static"
        && lib.hasSuffix ".1/24" lan.address
        && lan.gateway == ""
        && wan.gateway != ""
        && addresses.ipv4WithPrefix lan.address
        && addresses.ipv4WithPrefix wan.address
        && !overlaps
        && wan.dns != [ ];
      message = "The playground router needs static lan0 ending in .1/24 and wan0 on a different subnet with a gateway and DNS servers.";
    }
  ];

  networking = {
    nftables.enable = true;
    nat = {
      enable = true;
      externalInterface = "wan0";
      internalInterfaces = [ "lan0" ];
    };
    firewall = {
      filterForward = true;
      interfaces.lan0 = {
        allowedTCPPorts = [
          53
          3000
        ];
        allowedUDPPorts = [
          53
          67
        ];
      };
    };
  };

  services.adguardhome = {
    enable = true;
    host = lanIP;
    port = 3000;
    # Users, leases, query history and custom rules are runtime state.
    mutableSettings = true;
    settings = {
      dns = {
        bind_hosts = [
          "127.0.0.1"
          lanIP
        ];
        port = 53;
        upstream_dns = wan.dns;
        bootstrap_dns = wan.dns;
        allowed_clients = [
          "127.0.0.1"
          lanSubnet
        ];
      };
      dhcp = {
        enabled = true;
        interface_name = "lan0";
        local_domain_name = "playground.home.arpa";
        dhcpv4 = {
          gateway_ip = lanIP;
          subnet_mask = "255.255.255.0";
          range_start = "${prefix}.100";
          range_end = "${prefix}.200";
          lease_duration = 86400;
        };
      };
      filtering = {
        protection_enabled = true;
        filtering_enabled = true;
        blocking_mode = "null_ip";
      };
      filters = [
        {
          enabled = true;
          id = 1;
          name = "AdGuard DNS filter";
          url = "https://adguardteam.github.io/HostlistsRegistry/assets/filter_1.txt";
        }
      ];
    };
  };

  # The upstream module has no password-file option. Initialize one local admin
  # once, after its native YAML merge; never put a shared password in the recipe.
  systemd.services.adguardhome.preStart = lib.mkAfter ''
    if [ "$(${pkgs.yq-go}/bin/yq '(.users // []) | length' "$STATE_DIRECTORY/AdGuardHome.yaml")" = 0 ]; then
      umask 077
      password=$(${pkgs.openssl}/bin/openssl rand -base64 24)
      printf '%s\n' "$password" > "$STATE_DIRECTORY/initial-admin-password"
      hash=$(printf '%s\n' "$password" | ${pkgs.apacheHttpd}/bin/htpasswd -niBC 10 admin | ${pkgs.coreutils}/bin/cut -d: -f2)
      passwordHash="$hash" ${pkgs.yq-go}/bin/yq '.users = [{"name": "admin", "password": strenv(passwordHash)}]' "$STATE_DIRECTORY/AdGuardHome.yaml" > "$STATE_DIRECTORY/AdGuardHome.yaml.tmp"
      mv "$STATE_DIRECTORY/AdGuardHome.yaml.tmp" "$STATE_DIRECTORY/AdGuardHome.yaml"
    fi
  '';
  systemd.services.adguardhome.serviceConfig.StateDirectoryMode = "0700";

  # A fresh RPi can boot with an old clock: HTTPS filter downloads then fail.
  # Keep DHCP/DNS independent of NTP; retry once after the first successful sync.
  systemd.paths.playground-adguard-bootstrap = {
    wantedBy = [ "multi-user.target" ];
    pathConfig = {
      PathExists = "/run/systemd/timesync/synchronized";
      Unit = "playground-adguard-bootstrap.service";
    };
  };
  systemd.services.playground-adguard-bootstrap = {
    description = "Retry the initial AdGuard filter download after clock synchronization";
    after = [ "adguardhome.service" ];
    serviceConfig = {
      Type = "oneshot";
      # PathExists remains true; staying active prevents repeated restarts.
      RemainAfterExit = true;
    };
    script = ''
      if [ ! -s /var/lib/AdGuardHome/data/filters/1.txt ]; then
        ${pkgs.systemd}/bin/systemctl try-restart adguardhome.service
      fi
    '';
  };
}
