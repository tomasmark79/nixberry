{ pkgs, ... }:
{
  environment.systemPackages = with pkgs; [
    # Recipe management; other tools are selected by each configuration.
    git
  ];
}
