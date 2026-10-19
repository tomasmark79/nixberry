{ pkgs, ... }:
{
  environment.systemPackages = with pkgs; [
    # Příkazová řádka a systémové nástroje.
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
