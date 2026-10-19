# Compressed swap takes priority over the fallback disk swap file.
{
  zramSwap = {
    enable = true;
    algorithm = "zstd";
    # A 4 GiB RPi gets exactly 2 GiB; smaller VMs are capped by RAM size.
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
