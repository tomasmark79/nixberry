# RPi5 platform settings; the server configuration selects services.
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
