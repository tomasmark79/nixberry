# Disk format belongs to the instance, independently of its applications.
value:
assert
  builtins.elem value [
    "ext4"
    "btrfs"
  ]
  || throw "Root filesystem must be ext4 or btrfs.";
value
