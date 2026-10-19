# Příklad: uprav všechny identifikátory podle skutečného RPi a SSD.
# Potom zkopíruj do management/rpi/targets/rpi-demo.nix.
{
  hostName = "rpi-demo";
  bootLabel = "rpi-demo-boot";
  rootLabel = "rpi-demo-root";
  hardwareSerial = "0000000000000000";
  installerAddress = "192.168.50.20";
  address = "192.168.50.20/24";
  gateway = "192.168.50.1";
  dns = [ "192.168.50.1" ];
  lanMac = "02:00:00:00:00:20";
  diskDevice = "/dev/disk/by-id/ata-EXAMPLE_SERIAL";
  diskSerial = "EXAMPLE_SERIAL";
  rootGiB = 32;
  adminKeyFile = ../../../admin.pub;
}
