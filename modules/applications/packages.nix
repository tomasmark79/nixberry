{ pkgs, ... }:
{
  environment.systemPackages = with pkgs; [
    # Recipe management and generation comparisons; other tools are selected by each configuration.
    git
    nvd
  ];
}
