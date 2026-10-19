{
  # libvirt/QEMU a Proxmox: disk VirtIO a kanál QEMU Guest Agent.
  services.qemuGuest.enable = true;
  disko.devices.disk.system.device = "/dev/vda";
}
