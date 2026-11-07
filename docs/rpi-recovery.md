# Raspberry Pi 5 installation and recovery

[← Main guide](../README.md) · [Offline bundle](offline.md)

Declare the board and SSD once, then choose direct SSD preparation or recovery
SD installation. Both recreate the system; application data needs a separate
[backup restore](backups.md). Booting recovery media alone erases nothing.

## Declare the RPi and SSD

From the recipe root:

```sh
cp -i examples/rpi.nix machines/rpi/rpi-demo.nix
test -e admin.pub || cp ~/.ssh/id_ed25519.pub admin.pub
```

Replace every fictional identifier with your actual RPi, SSD and network values.
Keep an existing administrator key if it is correct; never copy a private key.

The filename without `.nix` determines the CLI target, for example
`nixberry show rpi-demo`. `hostName` determines the system hostname.
When changing it, preserve `bootLabel` and `rootLabel` for existing partitions;
otherwise they default from `hostName`. ext4 limits `rootLabel` to 16 characters.
The same 16-character limit is retained when selecting `rootFilesystem = "btrfs";`.
Ext4 remains the default. Btrfs uses separate root, snapshot and swap subvolumes;
see [filesystem selection](cli.md). Changing the declared format requires a
separately reviewed installation and does not authorize erasing an existing SSD.
Normal `update` refuses mismatched declared partition labels before activation;
it does not rename partitions. Inspect them with
`nixberry ssh rpi-demo -- lsblk -o NAME,FSTYPE,LABEL,PARTLABEL,MOUNTPOINTS`.

| Field | Example; replace with actual values |
| --- | --- |
| RPi serial | `0000000000000000` |
| LAN MAC | `02:00:00:00:00:20` |
| SSD system address | `192.168.50.20/24` |
| SSD serial | `EXAMPLE_SERIAL` |
| SSD by-id | `/dev/disk/by-id/ata-EXAMPLE_SERIAL` |

For the original flat declaration, `installerAddress` and `gateway` require IPv4
addresses. The persistent `address` requires IPv4 CIDR notation with a prefix
from 0 to 32; `dns` must contain at least one IPv4 or IPv6 address literal.

Another RPi or SSD needs its own declaration and newly built media.
Firmware must permit USB boot; this tool does not configure EEPROM.

## Prepare an attached SSD on Linux

This installs the ARM system and RPi boot files onto an SSD attached to the
management computer, without recovery media or a running RPi.

Use an unencrypted RPi target declaration. LUKS targets must use recovery media;
local installation refuses them rather than changing their security policy.
The supported workstation is x86_64 Linux with Nix, sudo and working
`aarch64-linux` binfmt emulation. On NixOS, declare
`boot.binfmt.emulatedSystems = [ "aarch64-linux" ];` in the workstation's own
configuration. Disk and installation tools come from the pinned recipe.

1. Connect the SSD and inspect it locally:

   ```sh
   lsblk -o NAME,PATH,MODEL,SERIAL,SIZE,TYPE,MOUNTPOINTS
   ls -l /dev/disk/by-id/
   ```

   Choose its **whole-disk** by-id path, not a `-partN` link. Unmount its
   partitions, close any file-manager window showing them, and disconnect
   unrelated removable disks. Its serial must match the target's `diskSerial`;
   a different USB enclosure may expose a different by-id path. `--local-disk`
   selects the workstation path without changing the target declaration.

2. Review the plan, then explicitly authorize writing:

   ```sh
   nixberry plan rpi-demo --local-disk /dev/disk/by-id/usb-EXAMPLE_SERIAL
   nixberry reproduce rpi-demo --local-disk /dev/disk/by-id/usb-EXAMPLE_SERIAL --apply
   ```

   For a previously used SSD, add `--erase-existing` to **both** commands after
   deciding its contents may be lost. Without `--apply`, no disk contents change.
   Even planning may ask for sudo to inspect signatures and mount namespaces.
   Nix may build or download the selected system and tools on the workstation.

The installer checks the disk serial/capacity, physical writable disk, partitions,
mounts, swap, holders and label collisions. It rechecks under a disk lock and
tests ARM execution before erasure. Installation uses private mounts, starts no
target services, and syncs/unmounts before reporting success.

The layout follows `rootGiB` from the declaration, with a 512 MiB boot partition;
remaining SSD space is left unused. Set `rootGiB` for the actual disk capacity
before installation if desired. This command does not resize existing systems.
Custom disk-layout overrides are refused; use recovery media for those layouts.
The target's hardware serial, network identities and EEPROM USB-boot support
must already be correct: they cannot be checked while the board is disconnected.

After successful installation, safely disconnect the SSD, attach it to the RPi
and boot from it. Run `nixberry verify rpi-demo` once it is reachable. An installed
ARM closure and generated boot files are not proof of physical firmware boot.
Application data, credentials and service identities still need their normal
backup restoration; this command does not clone a running server's mutable data.

If installation fails after partitioning, the SSD is incomplete. Correct the
reported problem and repeat the reviewed plan with `--erase-existing`; do not
boot the partial installation. The source server remains independent.

## Named physical interfaces

For a board with multiple Ethernet adapters, replace the flat `address`,
`gateway`, `dns`, `lanMac` and `reserveMac` fields with one `network` declaration:

```nix
network = {
  primary = "lan0";
  dns = [ "10.234.0.1" ];
  interfaces = {
    lan0 = {
      mac = "02:00:00:00:00:20";
      address = "10.123.0.1/24";
    };
    wan0 = {
      mac = "02:00:00:00:00:21";
      address = "10.234.0.20/24";
      gateway = "10.234.0.1";
    };
  };
};
```

`primary` selects the normal management address and MAC; it does not select the
default route. Here management uses `10.123.0.1`, while `wan0` supplies the
default gateway. `installerAddress` remains the address of the recovery
environment and can differ from both installed addresses.

Declare one to eight static interfaces, with unique unicast MACs and IPv4
addresses. Names become Linux interface names through MAC matching. Named RPi
interfaces support static IPv4 only. Each gateway is optional, with at most one
default gateway across the declaration. Global `network.dns` is required; it is
assigned to the gateway-owning interface by default. Other interfaces get no
per-link DNS unless their own `dns` list is supplied.
The declaration does not enable `GatewayOnLink` or create supplemental routes;
use a reachable gateway or supply explicit routing in the selected configuration.
Installed static adapters without a gateway receive their address even without
carrier. Network-online waits for the primary and gateway-owning adapters;
secondary adapters do not hold up that target.

Do not mix named and flat network fields. Existing flat declarations retain
their `enx<MAC>` names and leave the optional spare adapter unaddressed. Merely
declaring adapters does not enable routing, DHCP service or firewall access.

`network.recoveryInterface` optionally selects the bootstrap adapter and defaults
to `network.primary`. Named SD/RAM recovery uses DHCP only on that adapter;
secondary adapters have no address, DHCP or IPv6 router discovery. Recovery does
not activate the desired installed LAN/WAN addresses, gateway or DNS settings.
Read the actual recovery DHCP address and use `installerAddress` or `--address`
for that installation session.

Installed management, recovery DHCP and early-boot unlock can use different
interfaces/addresses. [RPi encryption](security.md#raspberry-pi-5) describes
`security.interface` and `security.address`. Reserve a separate unlock address
when the future installed LAN gateway is still occupied. Complete that gateway
handover before unlocking, because stage2 activates the installed addresses.
These selectors never authorize installation over an existing router or bypass
board/network identity checks.

Shared services can read the platform-independent
[declared network facts](reference.md#declared-network-facts). Those facts are
outputs, while hardware identities and addressing stay in this target file.

## Install from recovery SD

### 1. Export and write SD — management computer

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

### 2. Boot SD and restore SSD — RPi

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

#### Alternative: control the same recovery over SSH

Read the SD environment's current DHCP address and substitute `SD_IP`:

```sh
nixberry plan rpi-demo --address SD_IP --erase-existing
nixberry reproduce rpi-demo --address SD_IP --erase-existing --apply
```

This uses the same checks and installer. The SD address need not equal the
future SSD system address.

### 3. Finish by booting from SSD

The installer does not reboot automatically. Run `sudo poweroff` on the RPi,
remove SD after shutdown and power on with SSD connected.
On the **management computer**:

```sh
nixberry verify rpi-demo
```

See [troubleshooting](../README.md#troubleshooting) for login and new SSH keys.
The installed system has no configured console password.
Success includes the declared system, root and `/boot` on the correct SSD,
active SSH and networkd, and no failed units.

## Updating network declarations

Named physical networking supports fresh or separately reviewed recovery
installations, and ordinary updates when the running network already matches the
declaration. Before activating an update for a named RPi target, NixBerry checks
each declared adapter's MAC, Linux interface name, IPv4 address and prefix against
the running system. Existing flat declarations retain their identity checks.

An ordinary update cannot convert a running flat declaration to named adapters,
rename an adapter, or first establish a new declared address/prefix: those changes
fail the pre-activation check. Plan that transition separately. A recovery or
reinstallation procedure requires its own review of the board, storage and data;
installation may erase the SSD.

`security.interface` and `security.address` configure the installed initrd for the
next boot. They do not change the live NIC names or addresses, and they do not
bypass the update identity check. An explicit `--address` selects a connection
endpoint; the declared hardware and network checks still apply.

## Technical notes

Installation uses pinned disko and `nixos-install --system`.
`nixberry-rpi-install --stage` transfers the store without changing partitions.
The smaller `nixberry-rpi-network-installer` needs a target store transfer;
use the full SD for local offline recovery.
See [testing](test-plan.md) for content checks versus verified live boot.
