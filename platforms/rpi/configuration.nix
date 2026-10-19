# Platformní nastavení RPi5; služby vybírá konfigurace serveru.
{ config, ... }:
{
  imports = [
    ./options.nix
    ./system
  ];

  networking.hostName = config.nixberry.rpi.hostName;
  users.users.root.openssh.authorizedKeys.keyFiles = [ config.nixberry.rpi.adminKeyFile ];
  system.stateVersion = "26.05";
}
