# Each core supplies its own launcher with the exact library path baked in.
{ pkgs }:
let
  core = id: name: extensions: package: {
    inherit
      id
      name
      extensions
      package
      ;
    arguments = "--appendconfig /etc/retrogaming/paths.cfg --fullscreen";
  };
in
with pkgs.libretro;
[
  (core "nes" "Nintendo Entertainment System" "nes, zip, 7z" nestopia)
  (core "snes" "Super Nintendo" "sfc, smc, zip, 7z" snes9x)
  (core "gb" "Game Boy" "gb, zip, 7z" gambatte)
  (core "gbc" "Game Boy Color" "gbc, zip, 7z" gambatte)
  (core "gba" "Game Boy Advance" "gba, zip, 7z" mgba)
  (core "mastersystem" "Sega Master System" "sms, zip, 7z" genesis-plus-gx)
  (core "megadrive" "Sega Mega Drive" "md, gen, bin, zip, 7z" genesis-plus-gx)
  (core "gamegear" "Sega Game Gear" "gg, zip, 7z" genesis-plus-gx)
  (core "segacd" "Sega CD" "cue, chd, m3u" genesis-plus-gx)
  (core "atari2600" "Atari 2600" "a26, bin, zip, 7z" stella)
  (core "atari7800" "Atari 7800" "a78, zip, 7z" prosystem)
  (core "atarilynx" "Atari Lynx" "lnx, zip, 7z" handy)
  (core "pcengine" "PC Engine" "pce, cue, chd, m3u, zip, 7z" beetle-pce-fast)
  (core "psx" "PlayStation" "cue, chd, pbp, m3u" pcsx-rearmed)
  (core "n64" "Nintendo 64" "n64, z64, v64, zip, 7z" mupen64plus)
  (core "dreamcast" "Dreamcast" "gdi, cdi, chd, m3u" flycast)
  (core "arcade" "Arcade (FinalBurn Neo)" "zip, 7z" fbneo)
  (core "neogeo" "Neo Geo" "zip, 7z" fbneo)
  (core "dos" "DOS" "zip, dosz, conf" dosbox-pure)
  (core "doom" "Doom and Freedoom" "wad" prboom)
  (core "scummvm" "ScummVM" "scummvm" scummvm)
  (core "msx" "MSX" "rom, mx1, mx2, dsk, m3u" bluemsx)
  {
    id = "psp";
    name = "PlayStation Portable";
    extensions = "iso, cso, chd, pbp";
    package = pkgs.ppsspp;
    arguments = "--fullscreen";
  }
]
