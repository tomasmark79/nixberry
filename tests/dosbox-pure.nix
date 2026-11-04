# Run DOS instructions, not just the core's program-selection menu.
# A native 16 KiB Linux builder exercises the RPi5 executable-cache regression.
{ pkgs }:
let
  core = import ../modules/applications/retrogaming/dosbox-pure.nix { inherit pkgs; };
in
pkgs.runCommand "dosbox-pure-dynamic-execution"
  {
    nativeBuildInputs = [ pkgs.python3 ];
  }
  ''
    export XDG_CONFIG_HOME="$TMPDIR/config"
    export XDG_DATA_HOME="$TMPDIR/data"
    export XDG_CACHE_HOME="$TMPDIR/cache"
    export QT_QPA_PLATFORM=offscreen
    export SDL_VIDEODRIVER=dummy
    unset DBUS_SESSION_BUS_ADDRESS DISPLAY
    mkdir -p "$TMPDIR/saves" "$TMPDIR/states"
    python3 - <<'PY'
    import os
    from pathlib import Path
    # Original DOS COM: print a marker, then execute a tight instruction loop.
    Path(os.environ['TMPDIR'], 'TEST.COM').write_bytes(
        bytes.fromhex('b4 09 ba 09 01 cd 21 eb fe') + b'NixBerry DOS execution test$')
    PY
    cat > "$TMPDIR/core.opt" <<'EOF'
    dosbox_pure_cpu_core = "dynamic"
    dosbox_pure_midi = "disabled"
    EOF
    cat > "$TMPDIR/frontend.cfg" <<EOF
    video_driver = "null"
    audio_driver = "null"
    input_driver = "null"
    input_joypad_driver = "null"
    ui_companion_start_on_boot = "false"
    config_save_on_exit = "false"
    history_list_enable = "false"
    global_core_options = "true"
    core_options_path = "$TMPDIR/core.opt"
    system_directory = "$TMPDIR"
    savefile_directory = "$TMPDIR/saves"
    savestate_directory = "$TMPDIR/states"
    libretro_log_level = "0"
    EOF
    timeout 30 ${pkgs.lib.getExe core} --verbose --config "$TMPDIR/frontend.cfg" \
      --max-frames 120 "$TMPDIR/TEST.COM" > execution.log 2>&1 || {
      cat execution.log
      exit 1
    }
    if grep -Fq 'Setting execute permission on the code cache has failed' execution.log; then
      cat execution.log
      exit 1
    fi
    mkdir "$out"
    python3 -c 'import os; print(os.sysconf("SC_PAGESIZE"))' > "$out/host-page-size"
    cp execution.log "$out/"
  ''
