# History search and editing with scrolling support from the pinned fork.
{ pkgs, ... }:
{
  environment.systemPackages = [ (pkgs.callPackage ./package.nix { }) ];
  programs.bash.interactiveShellInit = ''
    export HSTR_CONFIG=raw-history-view,no-confirm
    export HSTR_TIOCSTI=n
    # Timestamps and line handling preserve multiline commands as one entry.
    shopt -s histappend cmdhist lithist
    export HISTTIMEFORMAT='%F %T '
    hstrnotiocsti() {
      # Include new commands from this and other shells.
      history -a
      history -n
      { READLINE_LINE="$( { </dev/tty command hstr -- "$READLINE_LINE"; } 2>&1 1>&3 3>&- )"; } 3>&1
      READLINE_POINT=''${#READLINE_LINE}
    }
    bind -x '"\C-r":hstrnotiocsti'
  '';
}
