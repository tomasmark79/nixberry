# Retro gaming console

The `retrogaming` configuration starts Pegasus in a local graphical session and
launches games through RetroArch or standalone PPSSPP. It uses the same NixBerry
configuration selection, updates and exports as other configurations.

## Select the configuration

For Raspberry Pi 5, start with [the fictional declaration](../examples/rpi-retrogaming.nix).
Copy it to `machines/rpi/rpi-retro.nix` and replace every board, disk and network
identifier. Use a distinct hostname and an unused client address. A retired
router's old gateway address and WAN settings are not appropriate for a console.
The declaration selects `configuration = "retrogaming";`.

The [RPi recovery guide](rpi-recovery.md) describes preparing installation media
and recovering an SSD. Review the selected board and disk before installation.
Building or exporting media does not install the console or prove HDMI output.

For a VM declaration:

```sh
nixberry new-libvirt-vm vm-demo-retrogaming --configuration retrogaming
```

The same configuration can be selected with `new-hyperv-vm`, `new-proxmox-vm`,
`new-virtualbox-vm` and `new-vmware-vm`. For an existing target, edit its stored
`configuration` field. Use the normal `show`, `plan`, `diff`, `update` and `export`
commands; there are no gaming-specific deployment commands.

After switching a running headless host, start `graphical.target` to open the
console immediately. Subsequent boots use the graphical target automatically:

```sh
systemctl start graphical.target
```

## Add games

The local session automatically signs in as `player`, an account without `wheel`
membership, administrator SSH keys or an enabled password. Its home is
`/var/lib/retrogaming`. Manage the machine using your existing administrator key.
Freedoom Phase 1 and Phase 2 are included as ready-to-play games in the
`Doom and Freedoom` collection. Their BSD-licensed data and PrBoom support WAD
come from pinned Nixpkgs sources. Other game ROMs and BIOS files are supplied
separately; the configuration installs emulators and creates their directories.

| Directory under `/var/lib/retrogaming/roms/` | System | Emulator |
| --- | --- | --- |
| `nes`, `snes` | NES, Super Nintendo | Nestopia, Snes9x |
| `gb`, `gbc`, `gba` | Game Boy, Color, Advance | Gambatte, mGBA |
| `mastersystem`, `megadrive`, `gamegear`, `segacd` | Sega 8/16-bit and CD | Genesis Plus GX |
| `atari2600`, `atari7800`, `atarilynx` | Atari consoles | Stella, ProSystem, Handy |
| `pcengine` | PC Engine, including CD | Beetle PCE Fast |
| `psx` | PlayStation | PCSX ReARMed |
| `n64` | Nintendo 64 | Mupen64Plus-Next |
| `dreamcast` | Dreamcast | Flycast |
| `arcade`, `neogeo` | Arcade, Neo Geo | FinalBurn Neo |
| `dos` | DOS | DOSBox Pure |
| `doom` | Doom, including the two bundled Freedoom campaigns | PrBoom |
| `scummvm` | ScummVM adventures | ScummVM |
| `msx` | MSX | blueMSX |
| `psp` | PlayStation Portable | PPSSPP SDL |

Upload games with the administrator account, then assign them to the player:

```sh
scp "My Game.nes" root@CONSOLE:/var/lib/retrogaming/roms/nes/
ssh root@CONSOLE 'chown player:users "/var/lib/retrogaming/roms/nes/My Game.nes"'
```

Restart Pegasus from its menu after adding files. Empty collections do not appear
until matching games exist. The declarative collection definitions are in
`/etc/xdg/pegasus-frontend/metafiles/nixberry.metadata.pegasus.txt`;
the player configuration's `metafiles` directory links to this managed file so
Pegasus discovers it automatically. Additional game metadata and artwork can
live beside ROMs. See the
[Pegasus metadata format](https://pegasus-frontend.org/docs/user-guide/meta-files/).

Disc collections accept their listed launch descriptors, such as `.cue`, `.gdi`,
`.chd` or `.m3u`, rather than listing every binary track as a separate game.
Keep all referenced tracks next to the descriptor. FinalBurn Neo needs compatible
ROM sets, including dependencies; arbitrary MAME sets are not interchangeable.
For ScummVM, place the game data in a subdirectory and add a `.scummvm` file
containing its ScummVM game ID. DOSBox Pure accepts game archives and `.conf`
files. The catalogue's `extensions` fields list the accepted formats per system.
DOSBox Pure expects ZIP/DOSZ archives, not 7-Zip files. Extract a `.7z` archive
into a temporary directory and create a ZIP with the same internal paths before
copying it to `roms/dos/`; retain the original archive separately. Its initial
program picker lets you select a game's executable or batch file.

The packaged DOSBox Pure core aligns its executable cache to the Linux host's
page size. This preserves dynamic CPU recompilation on both 4 KiB VM kernels
and the Raspberry Pi's 16 KiB kernel; the DOS guest still uses 4 KiB pages.
The CPU core can remain on `auto`, which selects the interpreter for real-mode
games and the dynamic recompiler for protected-mode games.

## BIOS, saves and settings

| Path under `/var/lib/retrogaming/` | Contents |
| --- | --- |
| `bios/` | RetroArch system files, preserving each core's required names/subdirectories |
| `saves/`, `states/` | RetroArch saves and save states, separated by core |
| `screenshots/` | RetroArch screenshots |
| `.config/retroarch/` | Writable preferences and controller remaps |
| `.config/pegasus-frontend/` | Pegasus preferences and controller mappings |
| `.config/ppsspp/` | PPSSPP configuration and its default PSP memory stick |

RetroArch's main settings file is created once. Later activations preserve it.
Package paths and save directories are reapplied when Pegasus launches a game,
so upgrades do not keep obsolete Nix store paths. The individual core launchers
pin the installed emulator library; the online core updater is disabled.
Back up the whole `/var/lib/retrogaming` directory, including hidden files and
PPSSPP's memory stick. Save states may be incompatible between emulator versions.

Snes9x, Genesis Plus GX and FinalBurn Neo have redistributable licenses with
noncommercial restrictions. Only these packages are allowed by this
configuration's narrow Nixpkgs license exception; other configurations retain
their existing policy.

## Xbox controller and sound

Start with a USB cable. RetroArch installs the upstream controller profiles and
uses SDL2 for gamepads. Press both stick buttons together to open the RetroArch
menu; use its quit action to return to Pegasus. Pegasus has its own controller
mapping screen. PPSSPP has separate controls and an exit action in its menu.

For Xbox models that support Bluetooth, use an administrator shell for initial
pairing while the controller's pairing indicator is flashing:

```sh
bluetoothctl
power on
agent on
default-agent
scan on
# Replace the address below with the controller found by the scan.
pair AA:BB:CC:DD:EE:FF
trust AA:BB:CC:DD:EE:FF
connect AA:BB:CC:DD:EE:FF
scan off
quit
```

Bluetooth is enabled at boot. Xbox 360 wireless controllers and Microsoft's USB
wireless adapter are different transports; their extra receiver drivers are not
configured here. Pairing, rumble and button mapping depend on the exact model
and firmware and need a physical test.

PipeWire handles sound. If HDMI is silent or the wrong output is selected, run
`pavucontrol` in the player session and select the connected HDMI device. An
administrator can use `systemctl status display-manager bluetooth` and
`journalctl -b -u display-manager` to investigate startup failures.

## Platforms and verification

Raspberry Pi 5 uses the platform's existing VC4 KMS overlay, adds Xorg's VC4
primary-GPU selection for HDMI, and enables the board's Bluetooth setup for
graphical configurations. These additions stay disabled for headless
configurations. DSI/composite displays are not configured by this HDMI setup.

All five VM backends select the same gaming module. Actual graphics acceleration,
audio, USB forwarding and Bluetooth availability depend on the hypervisor and
host; the configuration does not attach controllers or promise passthrough.
VM image/ISO and online/offline preparation retain their existing behavior.
RPi uses recovery SD media and has no VM ISO installation or reset operation.
Export includes the selected system, not the mutable game library.

N64, Dreamcast and PSP are included for experimentation; speed and compatibility
depend on each game, renderer and output resolution. This selection does not
claim PS2, GameCube or Wii support.

Run the launch-target check with:

```sh
nix build 'path:.#checks.x86_64-linux.retrogaming' --no-link
```

It checks the desktop account policy, installed launcher paths, writable settings
creation and Pegasus's discovery of the bundled games and a dummy game in an
isolated virtual display. Evaluating a
system confirms configuration consistency; building confirms artifacts can be
realized. Neither proves a physical RPi boot, accelerated rendering, HDMI audio,
controller operation or a game's performance. Test these after deployment,
including save persistence, returning to Pegasus, and a clean reboot.

The separate CPU regression check executes an original DOS COM program with
dynamic recompilation, rather than stopping at the executable-selection menu:

```sh
nix build 'path:.#checks.x86_64-linux.dosbox-pure' --no-link
nix build 'path:.#checks.aarch64-linux.dosbox-pure' --no-link
```

Its output records the builder's host page size. Run the ARM check natively on
a 16 KiB Raspberry Pi kernel to exercise that page-size boundary; an emulated
ARM build on a 4 KiB host does not prove it.
