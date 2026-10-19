{ lib, ... }:
{
  nix.settings.experimental-features = [
    "nix-command"
    "flakes"
  ];
  # Concurrency is sized for a server with 4 GB RAM.
  nix.settings.max-jobs = 1;
  nix.settings.cores = 4;
  # Unrar is unfree; do not allow other unfree packages.
  nixpkgs.config.allowUnfreePredicate = pkg: lib.getName pkg == "unrar";
}
