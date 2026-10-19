{
  # Všechny VM profily používají UEFI; Secure Boot zde není nakonfigurovaný.
  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;
  boot.initrd.availableKernelModules = [
    "virtio_pci"
    "virtio_blk"
    "virtio_scsi"
    "vmw_pvscsi"
    "hv_vmbus"
    "hv_storvsc"
    "ahci"
    "nvme"
    "sd_mod"
  ];
}
