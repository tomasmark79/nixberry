# Fictional console: replace every hardware, disk and network identifier before use.
{
  configuration = "retrogaming";
  hostName = "rpi-retro";
  hardwareSerial = "0000000000000000";
  bootLabel = "retro-boot";
  rootLabel = "retro-root";
  installerAddress = "192.168.50.30";
  address = "192.168.50.30/24";
  gateway = "192.168.50.1";
  dns = [ "192.168.50.1" ];
  lanMac = "02:00:00:00:00:30";
  diskDevice = "/dev/disk/by-id/ata-EXAMPLE_SERIAL";
  diskSerial = "EXAMPLE_SERIAL";
  rootGiB = 32;
  adminKeyFile = ../../admin.pub;
}
