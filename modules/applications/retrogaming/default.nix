{
  config,
  lib,
  pkgs,
  ...
}:
let
  home = "/var/lib/retrogaming";
  catalogue = import ./catalogue.nix { inherit pkgs; };
  # Explicit parents prevent root-owned implicit directories inside the player home.
  directories = [
    "roms"
    "bios"
    "saves"
    "states"
    "screenshots"
    ".config"
    ".config/retroarch"
    ".config/retroarch/config"
    ".config/retroarch/config/DOSBox-pure"
    ".config/pegasus-frontend"
    ".config/pegasus-frontend/metafiles"
  ]
  ++ map (entry: "roms/${entry.id}") catalogue;
in
{
  # These redistributable cores restrict commercial use; keep the exception local.
  nixpkgs.config.allowUnfreePredicate =
    package:
    builtins.elem (lib.getName package) [
      "libretro-snes9x"
      "libretro-genesis-plus-gx"
      "libretro-fbneo"
    ];
  users.users.player = {
    isNormalUser = true;
    description = "Retro gaming player";
    inherit home;
    createHome = true;
    extraGroups = [
      "video"
      "render"
    ];
  };

  hardware.graphics.enable = true;
  hardware.bluetooth = {
    enable = true;
    powerOnBoot = true;
    # Older Xbox BLE firmware needs identity distribution to complete bonding.
    settings.General.Privacy = lib.mkDefault "device";
  };
  services.udev.packages = [ pkgs.game-devices-udev-rules ];
  security.rtkit.enable = true;
  services.pipewire = {
    enable = true;
    alsa.enable = true;
    pulse.enable = true;
  };

  services.xserver = {
    enable = true;
    displayManager.lightdm.enable = true;
    windowManager.session = [
      {
        name = "retrogaming";
        start = ''
          ${pkgs.xset}/bin/xset s off
          ${pkgs.xset}/bin/xset -dpms
          ${pkgs.openbox}/bin/openbox &
          ${lib.getExe pkgs.pegasus-frontend} &
          waitPID=$!
        '';
      }
    ];
  };
  services.displayManager = {
    autoLogin = {
      enable = true;
      user = "player";
    };
    defaultSession = "none+retrogaming";
  };

  environment.systemPackages = [
    pkgs.pegasus-frontend
    pkgs.retroarch
    pkgs.ppsspp
    pkgs.pavucontrol
  ];
  environment.etc = {
    "xdg/pegasus-frontend/metafiles/nixberry.metadata.pegasus.txt".text = lib.concatMapStringsSep "\n" (
      entry: ''
        collection: ${entry.name}
        shortname: ${entry.id}
        directories: ${home}/roms/${entry.id}
        extensions: ${entry.extensions}
        launch: ${lib.getExe entry.package} ${entry.arguments} "{file.path}"
      '') catalogue;
    # Explicit games inherit all collections declared earlier in their file.
    # Keep bundled game records separate from the multi-collection definitions.
    "xdg/pegasus-frontend/metafiles/freedoom.metadata.pegasus.txt".text = ''
      collection: Doom and Freedoom
      shortname: doom
      game: Freedoom - Phase 1
      file: ${home}/roms/doom/freedoom1.wad
      launch: ${lib.getExe pkgs.libretro.prboom} --appendconfig /etc/retrogaming/paths.cfg --fullscreen "{file.path}"
      game: Freedoom - Phase 2
      file: ${home}/roms/doom/freedoom2.wad
      launch: ${lib.getExe pkgs.libretro.prboom} --appendconfig /etc/retrogaming/paths.cfg --fullscreen "{file.path}"
    '';
    # Reapplied at launch so package upgrades cannot leave stale store paths.
    "retrogaming/paths.cfg".text = ''
      assets_directory = "${pkgs.retroarch-assets}/share/retroarch/assets"
      joypad_autoconfig_dir = "${pkgs.retroarch-joypad-autoconfig}/share/libretro/autoconfig"
      libretro_info_path = "${pkgs.libretro-core-info}/share/retroarch/cores"
      system_directory = "${home}/bios"
      savefile_directory = "${home}/saves"
      savestate_directory = "${home}/states"
      screenshot_directory = "${home}/screenshots"
    '';
    # Seed once: controller remaps and preferences remain writable and persistent.
    "retrogaming/retroarch-defaults.cfg".text = ''
      menu_driver = "ozone"
      video_driver = "gl"
      audio_driver = "pulse"
      input_joypad_driver = "sdl2"
      input_autodetect_enable = "true"
      input_menu_toggle_gamepad_combo = "2"
      config_save_on_exit = "true"
      quit_press_twice = "true"
      menu_show_core_updater = "false"
      sort_savefiles_enable = "true"
      sort_savestates_enable = "true"
    '';
    "retrogaming/dosbox-pure-defaults.opt".text = ''
      dosbox_pure_cpu_core = "auto"
      dosbox_pure_midi = "GeneralUser-GS.sf2"
    '';
  };
  systemd.tmpfiles.rules =
    map (directory: "d ${home}/${directory} 0750 player users - -") directories
    ++ [
      "L+ ${home}/.config/pegasus-frontend/metafiles/nixberry.metadata.pegasus.txt - - - - /etc/xdg/pegasus-frontend/metafiles/nixberry.metadata.pegasus.txt"
      "L+ ${home}/.config/pegasus-frontend/metafiles/freedoom.metadata.pegasus.txt - - - - /etc/xdg/pegasus-frontend/metafiles/freedoom.metadata.pegasus.txt"
      "L+ ${home}/roms/doom/freedoom1.wad - - - - ${pkgs.freedoom}/share/games/doom/freedoom1.wad"
      "L+ ${home}/roms/doom/freedoom2.wad - - - - ${pkgs.freedoom}/share/games/doom/freedoom2.wad"
      "L+ ${home}/bios/prboom.wad - - - - ${pkgs.libretro.prboom.src}/prboom.wad"
      "L+ ${home}/bios/GeneralUser-GS.sf2 - - - - ${pkgs.soundfont-generaluser-gs}/share/soundfonts/GeneralUser-GS.sf2"
      "C ${home}/.config/retroarch/retroarch.cfg 0640 player users - ${
        config.environment.etc."retrogaming/retroarch-defaults.cfg".source
      }"
      "C ${home}/.config/retroarch/config/DOSBox-pure/DOSBox-pure.opt 0640 player users - ${
        config.environment.etc."retrogaming/dosbox-pure-defaults.opt".source
      }"
    ];
}
