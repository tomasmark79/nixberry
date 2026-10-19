# Shared server settings independent of architecture.
{
  imports = [
    ./locale.nix
    ./nix.nix
    ./swap.nix
    ./networking
  ];
}
