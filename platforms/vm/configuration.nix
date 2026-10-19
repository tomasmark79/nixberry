# Platformní nastavení x86_64 VM; služby vybírá konfigurace serveru.
{ ... }:
{
  imports = [
    ./system
  ];

  networking.hostName = "nixberry-vm";
  users.users.root.openssh.authorizedKeys.keyFiles = [ ../../admin.pub ];
  system.stateVersion = "26.05";
}
