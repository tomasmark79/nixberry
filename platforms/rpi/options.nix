# Parametry RPi jsou oddělené od společných služeb a od konfigurace VM.
{ config, lib, ... }:
let
  cfg = config.nixberry.rpi;
  inherit (lib) mkOption types;
  mac = types.strMatching "([0-9a-f]{2}:){5}[0-9a-f]{2}";
in
{
  options.nixberry.rpi = {
    hostName = mkOption {
      type = types.strMatching "[a-zA-Z0-9][a-zA-Z0-9-]*";
      description = "Název cílového RPi.";
    };
    hardwareSerial = mkOption {
      type = types.strMatching "[0-9a-f]{16}";
      description = "Sériové číslo RPi z device tree.";
    };
    installerAddress = mkOption {
      type = types.str;
      description = "IPv4 adresa vzdáleně dostupného RPi nebo instalační SD.";
    };
    address = mkOption {
      type = types.str;
      description = "Stálá adresa systému včetně délky prefixu.";
    };
    gateway = mkOption {
      type = types.str;
      description = "Výchozí brána cílového systému.";
    };
    dns = mkOption {
      type = types.listOf types.str;
      description = "DNS servery cílového systému.";
    };
    lanMac = mkOption {
      type = mac;
      description = "MAC hlavního síťového adaptéru.";
    };
    reserveMac = mkOption {
      type = types.nullOr mac;
      default = null;
      description = "MAC volitelného rezervního adaptéru.";
    };
    diskDevice = mkOption {
      type = types.strMatching "/dev/disk/by-id/[^/]+";
      description = "Stabilní by-id cesta cílového disku.";
    };
    diskSerial = mkOption {
      type = types.str;
      description = "Sériové číslo cílového disku z lsblk.";
    };
    rootGiB = mkOption {
      type = types.ints.positive;
      default = 256;
      description = "Velikost systémového oddílu v GiB.";
    };
    bootLabel = mkOption {
      type = types.strMatching "[A-Za-z0-9-]{1,36}";
      default = "${cfg.hostName}-boot";
      description = "GPT označení startovacího oddílu.";
    };
    rootLabel = mkOption {
      type = types.strMatching "[A-Za-z0-9-]{1,16}";
      default = "${cfg.hostName}-root";
      description = "Označení systémového oddílu a ext4.";
    };
    adminKeyFile = mkOption {
      type = types.path;
      description = "Soubor s veřejným klíčem správce.";
    };
  };
  config.assertions = [
    {
      assertion = cfg.reserveMac == null || cfg.reserveMac != cfg.lanMac;
      message = "Hlavní a rezervní adaptér RPi musí mít různé MAC.";
    }
    {
      assertion = cfg.installerAddress != "192.168.79.1";
      message = "Brouček nesmí být cílem instalace.";
    }
    {
      assertion = cfg.bootLabel != cfg.rootLabel;
      message = "Oddíly RPi musí mít různá označení.";
    }
  ];
}
