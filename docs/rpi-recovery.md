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

For the original flat declaration, `installerAddress` and `gateway` require IPv4
addresses. The persistent `address` requires IPv4 CIDR notation with a prefix
from 0 to 32; `dns` must contain at least one IPv4 or IPv6 address literal.

Another RPi or SSD needs its own declaration and newly built media.
Firmware must permit USB boot; this tool does not configure EEPROM.

### Named physical interfaces

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

The installed management address, recovery connection and early-boot SSH address
can have different roles. For a WAN-managed gateway that recovers and unlocks
over its LAN adapter, change the example's selectors and add the security fields:

```nix
network.primary = "wan0";
network.recoveryInterface = "lan0";
security.interface = "lan0";
security.address = "10.123.0.59/24";
```

The installed system keeps LAN `10.123.0.1/24` and WAN `10.234.0.20/24`. SD/RAM
recovery uses LAN DHCP. Installed initrd enables only LAN `10.123.0.59/24`, so it
can wait for manual unlock while another gateway still owns the future installed
LAN address. Reserve the early-boot address so no other device uses it.
Complete the gateway handover before unlocking: stage2 activates the declared
installed addresses after the encrypted root opens.
`security.address` is optional and must share the selected adapter's IPv4 subnet
and prefix; it changes neither installed addressing nor recovery DHCP. Without
it, initrd uses that adapter's installed address. Without `security.interface`,
initrd selects the primary adapter. See [physical encryption](security.md#raspberry-pi-5).
The protected installation/router-address checks remain in force; declaring a
future gateway address does not authorize contacting the existing router.

Shared services can read the platform-independent
[declared network facts](reference.md#declared-network-facts). Those facts are
outputs, while hardware identities and addressing stay in this target file.

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
