{ lib, ... }:
{
  nix.settings.experimental-features = [
    "nix-command"
    "flakes"
  ];
  # Souběh odpovídá serveru se 4 GB RAM.
  nix.settings.max-jobs = 1;
  nix.settings.cores = 4;
  # Unrar má nesvobodnou licenci; ostatní nesvobodné balíčky nepovolujeme.
  nixpkgs.config.allowUnfreePredicate = pkg: lib.getName pkg == "unrar";
}
