# Společná systémová nastavení serveru bez závislosti na architektuře.
{
  imports = [
    ./locale.nix
    ./nix.nix
    ./swap.nix
    ./networking
  ];
}
