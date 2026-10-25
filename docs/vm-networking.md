# VM network adapters

[Main guide](../README.md) · [VM platforms](vm.md) · [Declarations](reference.md)

The default is one adapter, with DHCP or static IPv4 selected by the target's
`network` field. Existing declarations keep their interface naming and addressing.
For multiple adapters, declare an ordered `spec.networkAdapters` list. Names become
Linux interface names, matched by MAC rather than enumeration order.

The **first adapter is the management interface**. Its MAC and attachment must
match `spec.mac` and the host's primary network settings. Top-level `network`
configures this adapter. Automatic discovery for libvirt, Hyper-V, Proxmox VE and
VirtualBox selects addresses belonging to its MAC. Do not use a production WAN
as a laboratory management network.

## Declare a libvirt gateway

Create a declaration with `nixberry new-libvirt-vm vm-demo-gateway --host HOST`,
then edit the ignored `instances/vm-demo-gateway.nix` before building:

```nix
{
  host = "local-libvirt";
  network = {
    mode = "dhcp";
    interfaces.lan0 = {
      mode = "static";
      address = "10.123.0.1/24";
    };
  };
  spec = {
    # Retain the generated name, UUID, owner and resource fields.
    mac = "52:54:00:12:34:01";
    networkAdapters = [
      { name = "wan0"; mac = "52:54:00:12:34:01"; network = "vm-demo-wan"; }
      { name = "lan0"; mac = "52:54:00:12:34:02"; network = "vm-demo-lan"; }
    ];
  };
}
```

This is an excerpt, not a complete replacement for the generated declaration.
The host connection's `network` must be `vm-demo-wan`. Prepare that NAT management
network and the isolated `vm-demo-lan` separately. NixBerry does not create them.
Additional libvirt attachments can use NAT or isolated networks; the first retains
the NAT requirement. All attached networks must be active. Creation checks each
declared MAC against existing domains and reservations in the attached networks.

Declaring two interfaces does **not** enable routing, NAT, DHCP service, DNS service
or firewall forwarding. Those are separate configuration roles. Attaching a lab
interface to a physical network can expose services; choose attachments deliberately.

## Addressing and identity

- There may be 1–8 adapters. Each needs a distinct nonzero unicast MAC and a unique
  name of 1–15 lowercase letters, digits or hyphens, starting with a letter. `lo`
  is reserved. Explicit adapter MACs must not reuse MACs elsewhere in the local VM inventory.
- Secondary `network.interfaces.NAME` entries support `disabled`, `dhcp` and
  `static`. The default is `disabled`: no DHCP, IPv6 router advertisements or
  link-local addressing. An omitted interface does not acquire an address.
- Secondary static IPv4 requires an address with prefix. Gateway and DNS are
  optional. Adding a gateway is explicit; a LAN interface normally needs none.
- The primary static configuration retains its required address, gateway and DNS.
- LUKS initrd networking matches only the primary MAC. Additional interfaces are
  configured after unlocking. LUKS encrypted ISO installation remains unsupported.
- Host identity checks cover every declared adapter and attachment and reject
  unexpected adapters. Guest verification also checks declared names/MACs and
  requires the SSH server address to belong to the first interface.

Changing the list does not hot-add, detach or reconnect existing VM hardware.
For an existing VM, a mismatch fails verification. A system update can change
addressing on already matching hardware; it does not authorize reinstallation
or network rewiring. Preserve recovery access when changing management addressing.

## Backend attachments

Every list entry contains `name`, `mac` and exactly the backend's attachment fields:

| Backend | Attachment fields | Existing host resources |
| --- | --- | --- |
| `libvirt` | `network` | Active libvirt networks; primary NAT, secondary NAT or isolated |
| `hyperv` | `switchName`, `switchId` | Existing switches, verified by name and UUID |
| `proxmox` | `bridge` | Existing Linux bridges on the declared Proxmox node |
| `virtualbox` | `bridge` | Existing bridged host adapters, using the same names reported by VBoxManage |
| `vmware` | `vmnet` | `nat` for default NAT, or `vmnet0`–`vmnet19` for operator-prepared custom networks |

The Hyper-V command accepts explicit switch and static MAC bindings; see
[Microsoft's adapter reference](https://learn.microsoft.com/en-us/powershell/module/hyper-v/add-vmnetworkadapter).
VMware custom networks must be prepared in its network editor; see
[Broadcom's Workstation network procedure](https://knowledge.broadcom.com/external/article/307810/creating-subnets-in-vmware-workstation.html).

Hyper-V MACs use twelve hexadecimal digits; other backends use colon-separated
octets. Libvirt retains its `52:54:00` prefix restriction. VMware retains the static
range `00:50:56:00:00:00`–`00:50:56:3f:ff:ff`. Primary VMware attachment defaults
to `nat`; an explicit `spec.vmnet` can select a custom primary network.

VirtualBox currently exposes bridged attachments through this contract. It does
not create internal or host-only networks. Proxmox and VMware custom-network
isolation is the operator's responsibility; a network's name does not prove its
forwarding or DHCP policy. VMware custom-network existence is not automatically
validated on every supported host operating system.

Workstation's `getGuestIPAddress` does not identify the adapter MAC. Its address
is a candidate; the subsequent guest check rejects a secondary-interface SSH
endpoint. If Tools selects that endpoint, automatic installation/update cannot
complete until Tools reports the management address. Do not bypass identity checks.
Workstation MAC collision checks cover dedicated storage and running VMs; stopped
VMs outside that storage cannot be inventoried globally. Other managed backends
check their available VM inventory before creation.

## Methods, exports and physical targets

The adapter list applies to both `image` and `iso`, including installer networking,
plans, manifests, status, verification and reset. Existing reset permission and
disk/reference checks remain required. Preview operations do not change VM hardware.

Offline exports retain the list and addressing in `manifest.json` and supply
`NETWORKS.txt`. Create every adapter in order and preserve the declared MACs when
importing these images. Manual import does not adopt ownership records. Legacy
exports without named adapters retain their flexible importer-assigned MAC policy.

No adapter CLI flag is needed: select a host when scaffolding, then edit the stored
declaration. CLI aliases and completion retain the same operations on all five
backends. Raspberry Pi 5 uses its physical target MAC bindings; it is not a VM and
does not accept `spec.networkAdapters`. Shared service configuration should consume
logical roles while platform declarations provide hardware identity.
