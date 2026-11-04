# Keep dynamic recompilation usable on Linux kernels with 4, 16 or 64 KiB pages.
{ pkgs }:
pkgs.libretro.dosbox-pure.overrideAttrs (old: {
  patches = (old.patches or [ ]) ++ [ ./dosbox-pure-host-pages.patch ];
})
