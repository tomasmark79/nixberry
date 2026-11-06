# Shared disko content for ordinary roots and the inside of LUKS roots.
{
  filesystem ? "ext4",
  label ? null,
}:
assert
  builtins.elem filesystem [
    "ext4"
    "btrfs"
  ]
  || throw "Root filesystem must be ext4 or btrfs.";
let
  extra =
    if label == null then
      { }
    else
      {
        extraArgs = [
          "-L"
          label
        ];
      };
  mount = mountpoint: {
    inherit mountpoint;
    mountOptions = [ "noatime" ];
  };
in
if filesystem == "ext4" then
  {
    type = "filesystem";
    format = "ext4";
  }
  // mount "/"
  // extra
else
  {
    type = "btrfs";
    # Snapshot storage is a sibling, so root snapshots cannot contain themselves.
    subvolumes = {
      "@root" = mount "/";
      "@snapshots" = mount "/.snapshots";
      # Active swap files must not be inside a snapshotted subvolume.
      "@swap" = mount "/swap";
    };
  }
  // extra
