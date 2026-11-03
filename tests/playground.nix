# Isolated clients prove DHCP, DNS filtering, NAT and WAN/LAN separation.
{ pkgs, emcx }:
pkgs.testers.runNixOSTest {
  name = "playground-router";
  node.pkgsReadOnly = false;
  node.specialArgs = { inherit emcx; };
  nodes = {
    router = { lib, ... }: {
      imports = [ ../configurations/playground.nix ];
      nixberry.network = {
        primaryInterface = "wan0";
        networkdUnits = {
          lan0 = "10-lan";
          wan0 = "10-wan";
        };
        interfaces = {
          lan0 = {
            name = "lan0";
            mac = "02:00:00:00:00:01";
            mode = "static";
            address = "192.168.192.1/24";
            gateway = "";
            dns = [ ];
          };
          wan0 = {
            name = "wan0";
            mac = "02:00:00:00:00:02";
            mode = "static";
            address = "192.168.79.2/24";
            gateway = "192.168.79.1";
            dns = [ "192.168.79.1" ];
          };
        };
      };
      networking.useDHCP = false;
      networking.useNetworkd = true;
      systemd.network = {
        enable = true;
        links = {
          "10-lan" = {
            matchConfig.OriginalName = "eth1";
            linkConfig.Name = "lan0";
          };
          "10-wan" = {
            matchConfig.OriginalName = "eth2";
            linkConfig.Name = "wan0";
          };
        };
        networks = {
          "10-lan" = {
            matchConfig.Name = "lan0";
            address = [ "192.168.192.1/24" ];
          };
          "10-wan" = {
            matchConfig.Name = "wan0";
            address = [ "192.168.79.2/24" ];
            gateway = [ "192.168.79.1" ];
          };
        };
      };
      # Test without downloading filters or contacting external DNS services.
      services.adguardhome.settings.filters = lib.mkForce [ ];
      services.adguardhome.settings.user_rules = [ "||ads.example.test^" ];
      virtualisation.vlans = [
        1
        2
      ];
      virtualisation.memorySize = 2048;
      environment.systemPackages = [ pkgs.curl ];
    };
    client = { ... }: {
      networking.useDHCP = false;
      networking.useNetworkd = true;
      systemd.network.networks."10-client" = {
        matchConfig.Name = "eth1";
        networkConfig.DHCP = "ipv4";
      };
      environment.systemPackages = [
        pkgs.curl
        pkgs.bind
      ];
      virtualisation.vlans = [ 1 ];
    };
    upstream = { ... }: {
      networking.useDHCP = false;
      networking.interfaces.eth1.ipv4.addresses = [
        {
          address = "192.168.79.1";
          prefixLength = 24;
        }
      ];
      services.dnsmasq = {
        enable = true;
        settings = {
          interface = "eth1";
          bind-interfaces = true;
          no-resolv = true;
          address = [ "/example.test/203.0.113.1" ];
        };
      };
      services.nginx = {
        enable = true;
        virtualHosts.default.locations."/".return = ''200 "$remote_addr"'';
      };
      networking.firewall.allowedUDPPorts = [ 53 ];
      networking.firewall.allowedTCPPorts = [
        53
        80
      ];
      environment.systemPackages = [
        pkgs.curl
        pkgs.bind
      ];
      virtualisation.vlans = [ 2 ];
    };
  };
  testScript = ''
    start_all()
    router.wait_for_unit("adguardhome.service", timeout=90)
    upstream.wait_for_unit("nginx.service")
    upstream.succeed("ip address add 203.0.113.1/32 dev lo")
    client.wait_until_succeeds("ip -4 addr show eth1 | grep -E '192.168.192.(1[0-9][0-9]|200)/24'")
    client.succeed("ip route | grep 'default via 192.168.192.1'")
    client.wait_until_succeeds("dig @192.168.192.1 example.test +short | grep -x 203.0.113.1")
    client.succeed("dig @192.168.192.1 ads.example.test +short | grep -x 0.0.0.0")
    assert client.succeed("curl -fsS --max-time 10 http://203.0.113.1/").strip() == "192.168.79.2"
    page = client.succeed("curl -fsSL --max-time 10 http://192.168.192.1:3000/")
    assert "<html" in page.lower(), page[:500]
    # No DNS or web administration is exposed through the WAN interface.
    upstream.fail("curl -fsS --connect-timeout 2 --max-time 3 http://192.168.79.2:3000/")
    assert not upstream.succeed("dig @192.168.79.2 example.test +short +time=1 +tries=1 || true").strip().startswith("203.0.113.1")
    upstream.succeed("ip route add 192.168.192.0/24 via 192.168.79.2")
    upstream.fail("curl -fsS --connect-timeout 2 --max-time 3 http://192.168.192.1:3000/")
    address = client.succeed("ip -4 -o addr show eth1 | awk '{print $4}' | cut -d/ -f1").strip()
    upstream.fail("ping -c 1 -W 1 " + address)
    # Credentials are generated privately and survive ordinary service restarts.
    router.succeed("test -s /var/lib/AdGuardHome/initial-admin-password; test $(stat -c %a /var/lib/AdGuardHome/initial-admin-password) = 600")
    before = router.succeed("sha256sum /var/lib/AdGuardHome/initial-admin-password")
    router.succeed("systemctl restart adguardhome")
    router.wait_for_unit("adguardhome.service")
    assert router.succeed("sha256sum /var/lib/AdGuardHome/initial-admin-password") == before
    router.wait_for_open_port(3000, addr="192.168.192.1")
    status = router.succeed("curl -s -o /dev/null -w '%{http_code}' http://192.168.192.1:3000/control/status").strip()
    assert status in ("401", "403", "302"), status
    router.succeed("printf 'user = \"admin:%s\"\\n' \"$(cat /var/lib/AdGuardHome/initial-admin-password)\" | curl --config - -fsS http://192.168.192.1:3000/control/status >/dev/null")
  '';
}
