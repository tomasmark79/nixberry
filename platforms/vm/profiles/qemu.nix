{
  # libvirt/QEMU and Proxmox: VirtIO disk and QEMU Guest Agent channel.
  services.qemuGuest.enable = true;
  disko.devices.disk.system.device = "/dev/vda";
}
