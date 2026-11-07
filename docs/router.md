# RPi router: DHCP and AdGuard Home

NAT, DHCP, DNS ad blocking and AdGuard Home administration, without mail,
web hosting or production application data. Each RPi has its own declaration;
`configurations/router.nix` selects the shared router module.

## 1. Copy and edit

Run from the NixBerry checkout on the management computer. No hypervisor is needed.

```sh
export PATH="$PWD:$PATH"
export NIXBERRY_RECIPE="$PWD"
cp -i examples/rpi-router.nix machines/rpi/rpi-demo-router.nix
test -e admin.pub || cp ~/.ssh/id_ed25519.pub admin.pub
```

The example contains the author's board and SSD identifiers, published with
permission, and fictional MAC addresses. **Replace all identities for your
machine.** Never copy a private key.
Keep an existing `admin.pub` if it already contains your administrator key.

| Edit in the copied file | Where to find it / meaning |
| --- | --- |
| `hardwareSerial` | On the RPi: `tr -d '\000' </proc/device-tree/serial-number` |
| `diskDevice`, `diskSerial` | `ls -l /dev/disk/by-id/` and `lsblk -d -o PATH,MODEL,SERIAL,SIZE` |
| `rootGiB` | Root size; leave at least 520 MiB for boot and partition metadata |
| Interface `mac` values | On the RPi: `ip -br link`; names follow MACs, not USB discovery order |
| `lan0.address` | Dedicated downstream network; use an address ending in `.1/24` |
| `wan0.address`, `gateway`, `network.dns` | A free static address, router and DNS in the existing upstream network |
| `installerAddress` | Temporary recovery-medium address, used only by the media installation path |

The sample uses onboard Ethernet as **LAN `192.168.192.1/24`**, and USB Ethernet
as **WAN `192.168.79.2/24`**, gateway `192.168.79.1`. Management uses `wan0`.
LAN DHCP automatically uses `.100`–`.200`, with the router as gateway and
DNS server. LAN and WAN must be different subnets. WAN currently uses static
IPv4; choose an upstream address outside its DHCP pool or reserve it there.

## 2. Prepare the SSD

On an x86_64 Linux workstation with ARM binfmt enabled, attach the intended SSD:

```sh
nixberry plan rpi-demo-router --local-disk /dev/disk/by-id/YOUR_SSD --erase-existing
nixberry reproduce rpi-demo-router --local-disk /dev/disk/by-id/YOUR_SSD --erase-existing --apply
```

`--apply --erase-existing` erases that SSD after identity checks. Use its local
whole-disk by-id path, not a `-partN` path. See [direct SSD preparation](rpi-recovery.md#prepare-an-attached-ssd-on-linux)
for prerequisites, or use the existing [SD/USB installation path](rpi-recovery.md#install-from-recovery-sd).

## 3. Connect and use

Attach the completed SSD to the RPi. Connect **USB/WAN to the existing home
network** and **onboard/LAN to a test client or separate switch**. Never connect
the router LAN to the existing home LAN: it runs its own DHCP server.
Boot the RPi, then from the upstream network:

```sh
nixberry verify rpi-demo-router
nixberry ssh rpi-demo-router -- cat /var/lib/AdGuardHome/initial-admin-password
```

On a downstream client using automatic addressing, open
**http://192.168.192.1:3000/** and log in as **admin**, using that generated
password. The password is unique, created at first start and kept outside Git
and the Nix store. Change it through AdGuard Home if desired; the file then
still contains only the initial password. The web UI and DNS
are available on LAN only; SSH management is also available through WAN.
If a fresh RPi boots with an incorrect clock, a missing default filter is retried
automatically after NTP synchronization. DHCP/DNS do not wait for the clock;
the retry briefly restarts AdGuard once, and leaves an existing filter alone.

## Change or make another one

Edit addresses/MACs in the target declaration, then:

```sh
nixberry diff rpi-demo-router
nixberry update rpi-demo-router --apply
nixberry verify rpi-demo-router
```

Nix owns the network, DHCP range, upstream DNS and default filter list. AdGuard's
UI owns users and custom filtering rules; its state lives in `/var/lib/AdGuardHome` and needs
its own backup if valuable. To create another independent router, copy the
example to a new target filename and change its identities and addresses.
To add your own services, create a small named configuration importing
`modules/common.nix` and `modules/services/router.nix`, and select it in that
target. No fork of the running Broucek configuration is needed.
