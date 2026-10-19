# Komprimovaný swap má přednost před záložním swap souborem na disku.
{
  zramSwap = {
    enable = true;
    algorithm = "zstd";
    # Na RPi se 4 GiB RAM zajistí limit přesně 2 GiB; menší VM omezí RAM.
    memoryPercent = 100;
    memoryMax = 2 * 1024 * 1024 * 1024;
    priority = 100;
  };

  swapDevices = [
    {
      device = "/swapfile";
      size = 8 * 1024;
      priority = 10;
    }
  ];
}
