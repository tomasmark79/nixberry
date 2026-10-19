{ pkgs, ... }:
{
  environment.systemPackages = with pkgs; [
    # Command line and system tools.
    git
    bat
    btop
    chafa
    fastfetch
    file
    htop
    lnav
    lsof
    ncdu
    nvd
    screen
    strace
    tldr
    tmux
    translate-shell
    wget
    traceroute

    # Archivy.
    p7zip
    unrar
    unzip
    zip

    yazi
  ];
}
