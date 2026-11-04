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
  seed = cfg.environment.etc."retrogaming/retroarch-defaults.cfg".source;
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
assert lib.assertMsg (builtins.any (rule: lib.hasSuffix " ${seed}" rule)
  cfg.systemd.tmpfiles.rules
) "The writable settings seed must copy the regular store file, not an /etc symlink.";
assert lib.assertMsg (builtins.any (
  entry: entry.id == "doom"
) catalogue) "The installed starter games must have an emulator collection.";
assert lib.assertMsg
  (builtins.elem "d /var/lib/retrogaming/roms 0750 player users - -" cfg.systemd.tmpfiles.rules)
  "The ROM parent must belong to the player before tmpfiles traverses its collections.";
assert lib.assertMsg (lib.hasInfix ''dosbox_pure_midi = "GeneralUser-GS.sf2"''
  cfg.environment.etc."retrogaming/dosbox-pure-defaults.opt".text
) "DOS MIDI must use the installed internal soundfont.";
pkgs.runCommand "retrogaming-launch-targets"
  {
    nativeBuildInputs = [
      pkgs.xorg-server
      pkgs.systemd
    ];
  }
  ''
    ${lib.concatMapStringsSep "\n" (entry: ''
      test -x ${lib.getExe entry.package}
    '') catalogue}
    test -s ${cfg.environment.etc."xdg/pegasus-frontend/metafiles/nixberry.metadata.pegasus.txt".source}
    test -s ${pkgs.libretro.prboom.src}/prboom.wad
    test -s ${pkgs.freedoom}/share/games/doom/freedoom1.wad
    test -s ${pkgs.freedoom}/share/games/doom/freedoom2.wad

    # NixOS /etc entries are symlinks. A C rule must seed a regular, writable
    # preferences file rather than copying the /etc symlink into the home.
    seedRoot="$TMPDIR/seed-root"
    mkdir -p "$seedRoot/etc/retrogaming" "$seedRoot${builtins.dirOf (toString seed)}" \
      "$seedRoot/var/lib/retrogaming/.config/retroarch"
    cp ${seed} "$seedRoot${seed}"
    ln -s ${seed} "$seedRoot/etc/retrogaming/retroarch-defaults.cfg"
    systemd-tmpfiles --root="$seedRoot" --create ${
      pkgs.writeText "retrogaming-seed-test.conf" (
        lib.concatStringsSep "\n" (
          map (rule: lib.replaceStrings [ " player users " ] [ " - - " ] rule) (
            builtins.filter (
              rule: lib.hasPrefix "C /var/lib/retrogaming/.config/retroarch/retroarch.cfg " rule
            ) cfg.systemd.tmpfiles.rules
          )
        )
      )
    }
    test ! -L "$seedRoot/var/lib/retrogaming/.config/retroarch/retroarch.cfg"
    printf '\n# Persistent user preference\n' >> "$seedRoot/var/lib/retrogaming/.config/retroarch/retroarch.cfg"

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
    ln -s ${pkgs.freedoom}/share/games/doom/freedoom1.wad "$TMPDIR/roms/doom/freedoom1.wad"
    ln -s ${pkgs.freedoom}/share/games/doom/freedoom2.wad "$TMPDIR/roms/doom/freedoom2.wad"
    sed "s|/var/lib/retrogaming|$TMPDIR|g" \
      ${
        cfg.environment.etc."xdg/pegasus-frontend/metafiles/nixberry.metadata.pegasus.txt".source
      } > "$TMPDIR/managed.metadata.pegasus.txt"
    ln -s "$TMPDIR/managed.metadata.pegasus.txt" "$XDG_CONFIG_HOME/pegasus-frontend/metafiles/nixberry.metadata.pegasus.txt"
    sed "s|/var/lib/retrogaming|$TMPDIR|g" \
      ${
        cfg.environment.etc."xdg/pegasus-frontend/metafiles/freedoom.metadata.pegasus.txt".source
      } > "$TMPDIR/freedoom.metadata.pegasus.txt"
    ln -s "$TMPDIR/freedoom.metadata.pegasus.txt" "$XDG_CONFIG_HOME/pegasus-frontend/metafiles/freedoom.metadata.pegasus.txt"
    mkdir -p "$XDG_CONFIG_HOME/pegasus-frontend/themes/verification"
    cp ${./retrogaming-theme.qml} "$XDG_CONFIG_HOME/pegasus-frontend/themes/verification/theme.qml"
    printf 'name: NixBerry Verification\nauthor: NixBerry\n' > "$XDG_CONFIG_HOME/pegasus-frontend/themes/verification/theme.cfg"
    printf 'general.theme: %s\n' "$XDG_CONFIG_HOME/pegasus-frontend/themes/verification" > "$XDG_CONFIG_HOME/pegasus-frontend/settings.txt"
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
      if grep -Fq 'NIXBERRY_CATALOGUE_VALID' pegasus.log; then discovered=true; break; fi
      kill -0 "$playerPid" || break
      sleep 0.1
    done
    if ! $discovered; then cat xvfb.log pegasus.log; exit 1; fi
    touch "$out"
  ''
