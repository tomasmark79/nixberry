# Evaluate the desktop policy and check every generated emulator launch target.
{ pkgs }:
let
  inherit (pkgs) lib;
  evaluated = import (pkgs.path + "/nixos/lib/eval-config.nix") {
    system = pkgs.stdenv.hostPlatform.system;
    modules = [
      ../configurations/retrogaming.nix
      {
        boot.loader.grub.enable = false;
        fileSystems."/" = {
          device = "/dev/vda";
          fsType = "ext4";
        };
        system.stateVersion = "26.05";
      }
    ];
  };
  cfg = evaluated.config;
  catalogue = import ../modules/applications/retrogaming/catalogue.nix { pkgs = evaluated.pkgs; };
in
assert lib.assertMsg (
  cfg.services.displayManager.autoLogin.user == "player"
) "The console must use its dedicated player.";
assert lib.assertMsg (
  !(builtins.elem "wheel" cfg.users.users.player.extraGroups)
) "The player must not be an administrator.";
assert lib.assertMsg cfg.networking.firewall.enable "Gaming must preserve the firewall.";
assert lib.assertMsg (
  !cfg.services.openssh.settings.PasswordAuthentication
) "Gaming must preserve key-only SSH.";
assert lib.assertMsg (
  cfg.services.displayManager.defaultSession == "none+retrogaming"
) "The console must start its gaming session.";
assert lib.assertMsg (
  builtins.length catalogue >= 20
) "The console must provide the broad emulator selection.";
assert lib.assertMsg (builtins.any
  (
    rule:
    lib.hasPrefix "L+ /var/lib/retrogaming/.config/pegasus-frontend/metafiles/nixberry.metadata.pegasus.txt " rule
  )
  cfg.systemd.tmpfiles.rules
) "Pegasus must discover the managed catalogue in the player's writable configuration directory.";
assert lib.assertMsg (lib.hasInfix ''input_menu_toggle_gamepad_combo = "2"''
  cfg.environment.etc."retrogaming/retroarch-defaults.cfg".text
) "Both stick buttons must open the emulator menu.";
pkgs.runCommand "retrogaming-launch-targets"
  {
    nativeBuildInputs = [ pkgs.xorg-server ];
  }
  ''
    ${lib.concatMapStringsSep "\n" (entry: ''
      test -x ${lib.getExe entry.package}
    '') catalogue}
    test -s ${cfg.environment.etc."xdg/pegasus-frontend/metafiles/nixberry.metadata.pegasus.txt".source}

    # Exercise pinned Pegasus's actual discovery path, without a game collection
    # or a physical display. The dummy file tests indexing, not emulation.
    export XDG_CONFIG_HOME="$TMPDIR/config"
    export XDG_DATA_HOME="$TMPDIR/data"
    export XDG_CACHE_HOME="$TMPDIR/cache"
    export XDG_RUNTIME_DIR="$TMPDIR/runtime"
    export QT_QPA_PLATFORM=xcb
    export QT_QUICK_BACKEND=software
    export LIBGL_ALWAYS_SOFTWARE=1
    export DISPLAY=:99
    mkdir -p "$XDG_CONFIG_HOME/pegasus-frontend/metafiles" "$TMPDIR/roms/nes" "$XDG_RUNTIME_DIR"
    chmod 700 "$XDG_RUNTIME_DIR"
    touch "$TMPDIR/roms/nes/Discovery Test.nes"
    ${lib.concatMapStringsSep "\n" (entry: ''mkdir -p "$TMPDIR/roms/${entry.id}"'') catalogue}
    sed "s|/var/lib/retrogaming|$TMPDIR|g" \
      ${
        cfg.environment.etc."xdg/pegasus-frontend/metafiles/nixberry.metadata.pegasus.txt".source
      } > "$TMPDIR/managed.metadata.pegasus.txt"
    ln -s "$TMPDIR/managed.metadata.pegasus.txt" "$XDG_CONFIG_HOME/pegasus-frontend/metafiles/nixberry.metadata.pegasus.txt"
    Xvfb "$DISPLAY" -screen 0 1280x720x24 -nolisten tcp -ac > xvfb.log 2>&1 &
    displayPid=$!
    playerPid=
    trap 'test -z "$playerPid" || kill -KILL "$playerPid" 2>/dev/null; kill "$displayPid" 2>/dev/null || true' EXIT
    # Wait for Xvfb's socket before starting Qt.
    for attempt in $(seq 1 100); do
      test ! -S /tmp/.X11-unix/X99 || break
      sleep 0.1
    done
    ${lib.getExe evaluated.pkgs.pegasus-frontend} > pegasus.log 2>&1 &
    playerPid=$!
    discovered=false
    for attempt in $(seq 1 100); do
      if grep -Fq '1 games found' pegasus.log; then discovered=true; break; fi
      kill -0 "$playerPid" || break
      sleep 0.1
    done
    if ! $discovered; then cat xvfb.log pegasus.log; exit 1; fi
    touch "$out"
  ''
