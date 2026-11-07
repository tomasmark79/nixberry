# Board/SSD example published with its owner's permission; MACs are fictional.
# Replace the board/SSD identities and interface MACs for YOUR hardware.
{
  configuration = "router";
  hostName = "rpi-demo-router";
  hardwareSerial = "131f2412d4d5bd06";
  bootLabel = "router-boot";
  rootLabel = "router-root";
  diskDevice = "/dev/disk/by-id/ata-Patriot_Burst_A32D0711017000104638";
  diskSerial = "A32D0711017000104638";
  rootGiB = 256;
  adminKeyFile = ../../admin.pub;
  security.mode = "none";
  installerAddress = "192.168.79.2";
  network = {
    # Manage the router from the upstream/home network over its WAN interface.
    primary = "wan0";
    recoveryInterface = "wan0";
    dns = [
      "1.1.1.1"
      "9.9.9.9"
    ];
    interfaces = {
      # Onboard Ethernet: dedicated test LAN, DHCP .100-.200 and DNS blocking.
      lan0 = {
        mac = "02:00:00:00:00:01";
        address = "192.168.192.1/24";
      };
      # USB Ethernet: connect to the existing router, using a free STATIC IP.
      wan0 = {
        mac = "02:00:00:00:00:02";
        address = "192.168.79.2/24";
        gateway = "192.168.79.1";
      };
    };
  };
}
