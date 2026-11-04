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
    # Original DOS COM: print, send a General MIDI note, then run an instruction loop.
    code = bytearray.fromhex(
        'b4 09 ba 00 00 cd 21 ba 31 03 b0 3f ee 4a b0 90 ee b0 3c ee b0 64 ee eb fe')
    code[3:5] = (0x100 + len(code)).to_bytes(2, 'little')
    Path(os.environ['TMPDIR'], 'TEST.COM').write_bytes(code + b'NixBerry DOS execution test$')
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
    # Use the core's internal synthesizer; no frontend MIDI device is needed.
    ln -s ${pkgs.soundfont-generaluser-gs}/share/soundfonts/GeneralUser-GS.sf2 "$TMPDIR/GeneralUser-GS.sf2"
    sed -i 's/dosbox_pure_midi = "disabled"/dosbox_pure_midi = "GeneralUser-GS.sf2"/' "$TMPDIR/core.opt"
    timeout 30 ${pkgs.lib.getExe core} --verbose --config "$TMPDIR/frontend.cfg" \
      --max-frames 60 "$TMPDIR/TEST.COM" > midi.log 2>&1 || {
      cat midi.log
      exit 1
    }
    grep -Fq 'MIDI: Opened device:tsf' midi.log || { cat midi.log; exit 1; }
    mkdir "$out"
    python3 -c 'import os; print(os.sysconf("SC_PAGESIZE"))' > "$out/host-page-size"
    cp execution.log "$out/"
    cp midi.log "$out/"
  ''
