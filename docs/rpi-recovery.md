# Raspberry Pi 5 recovery from SD

[← Main guide](../README.md) · [Offline bundle](offline.md)

This procedure restores an SSD from a full SD image. Booting the card alone
erases nothing. Steps distinguish the management computer from the RPi.

## 1. Check the declaration and prepare SD — management computer

Copy the [RPi template](../management/examples/rpi.nix) into
`management/rpi/targets/rpi-demo.nix`. Replace its fictional identifiers with
the actual RPi and SSD values, and prepare your own `admin.pub`.

The filename without `.nix` determines the CLI target, for example
`nixberry show rpi-demo`. `hostName` determines the system hostname.
When changing it, preserve `bootLabel` and `rootLabel` for existing partitions;
otherwise they default from `hostName`. ext4 limits `rootLabel` to 16 characters.
Normal `update` does not rename partitions. Inspect them with
`nixberry ssh rpi-demo -- lsblk -o NAME,FSTYPE,LABEL,PARTLABEL,MOUNTPOINTS`.

| Field | Example; replace with actual values |
| --- | --- |
| RPi serial | `0000000000000000` |
| LAN MAC | `02:00:00:00:00:20` |
| SSD system address | `192.168.50.20/24` |
| SSD serial | `EXAMPLE_SERIAL` |
| SSD by-id | `/dev/disk/by-id/ata-EXAMPLE_SERIAL` |

Another RPi or SSD needs its own declaration and newly built media.
Firmware must permit USB boot; this tool does not configure EEPROM.

Export from the project root:

```sh
nixberry export rpi-demo --output ~/rpi-demo-offline
```

Uncompressed image size varies. Inspect with `zstd -l` and compare with
card capacity from `lsblk -b`.

Writing erases the selected card. Identify it by capacity, reader and partitions,
unmount its partitions and write the **entire uncompressed image**. Complete
`fsync`, flush disk caches and compare all written bytes against the image
before safely removing the card. Preserve the previous working installer SD.
A fixed example such as `/dev/sda` is not safe device identification.

## 2. Boot SD and restore SSD — RPi

Power off the RPi, insert SD and boot. If firmware prefers SSD, leave SSD
disconnected during boot and connect it inside the installation environment.
`findmnt /` must show SD, usually `/dev/mmcblk0p2`.

On the console of the **RPi booted from SD**:

```sh
sudo nixberry-install-local --target rpi-demo --dry-run --erase-existing
sudo nixberry-install-local --target rpi-demo --apply --erase-existing
```

The first command must pass before the second. The second **erases the verified
SSD** and installs from the local store without internet.
`--target` is mandatory even on the RPi. Checks cover model, serial, MAC, SSD,
capacity, manifest and mounted partitions. A mounted system SSD cannot be
overwritten; `--erase-existing` does not bypass this protection.

### Alternative: control the same recovery over SSH

Read the SD environment's current DHCP address and substitute `SD_IP`:

```sh
nixberry plan rpi-demo --address SD_IP --erase-existing
nixberry reproduce rpi-demo --address SD_IP --erase-existing --apply
```

This uses the same checks and installer. The SD address need not equal the
future SSD system address.

## 3. Finish by booting from SSD

The installer does not reboot automatically. Run `sudo poweroff` on the RPi,
remove SD after shutdown and power on with SSD connected.
On the **management computer**:

```sh
nixberry verify rpi-demo
```

See [troubleshooting](../README.md#troubleshooting) for login and new SSH keys.
The installed system has no configured console password.
Success includes the declared system, root on the correct SSD, active SSH and
networkd, and no failed units.

## Technical notes

Installation uses pinned disko and `nixos-install --system`.
`nixberry-rpi-install --stage` transfers the store without changing partitions.
The smaller `nixberry-rpi-network-installer` needs a target store transfer;
use the full SD for local offline recovery.
See [testing](test-plan.md) for content checks versus verified live boot.
