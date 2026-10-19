# Vyhledávání a úpravy historie s posouváním z vlastního forku.
{ pkgs, ... }:
{
  environment.systemPackages = [ (pkgs.callPackage ./package.nix { }) ];
  programs.bash.interactiveShellInit = ''
    export HSTR_CONFIG=raw-history-view,no-confirm
    export HSTR_TIOCSTI=n
    # Časové značky a zalomení zachovají víceřádkový příkaz jako jeden záznam.
    shopt -s histappend cmdhist lithist
    export HISTTIMEFORMAT='%F %T '
    hstrnotiocsti() {
      # Zpřístupníme i nové příkazy z tohoto a ostatních shellů.
      history -a
      history -n
      { READLINE_LINE="$( { </dev/tty command hstr -- "$READLINE_LINE"; } 2>&1 1>&3 3>&- )"; } 3>&1
      READLINE_POINT=''${#READLINE_LINE}
    }
    bind -x '"\C-r":hstrnotiocsti'
  '';
}
