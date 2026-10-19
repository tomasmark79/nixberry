# x86_64 VM platform settings; the server configuration selects services.
{ ... }:
{
  imports = [
    ./system
  ];

  networking.hostName = "nixberry-vm";
  users.users.root.openssh.authorizedKeys.keyFiles = [ ../../admin.pub ];
  system.stateVersion = "26.05";
}
