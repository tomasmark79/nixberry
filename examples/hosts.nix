# Templates, not active hosts. Adjust for your environment and copy selected
# entries into hosts.local.nix. Addresses are examples only.
{
  local-libvirt = {
    backend = "libvirt";
    connection = {
      uri = "qemu:///system";
      storagePool = "default";
      storageDirectory = "/var/lib/libvirt/images";
      network = "default";
    };
  };
  hyperv-windows-example = {
    backend = "hyperv";
    connection = {
      sshHost = "192.168.50.10";
      sshUser = "operator";
      windowsComputerName = "WINDOWS-DEMO";
      storageDirectory = "C:\\ProgramData\\nixberry\\hyperv";
      switchName = "Default Switch";
      switchId = "00000000-0000-0000-0000-000000000000";
    };
  };
  pve-example = {
    backend = "proxmox";
    connection = {
      transport = "ssh";
      hostPlatform = "linux";
      hostName = "pve-example";
      sshHost = "192.168.50.120";
      sshUser = "root";
      pythonCommand = "python3";
      storageDirectory = "/var/lib/nixberry";
      node = "pve-example";
      storage = "local-lvm";
      isoStorage = "local"; # Existing storage with iso content enabled.
      bridge = "vmbr0";
    };
  };
  virtualbox-linux-example = {
    backend = "virtualbox";
    connection = {
      transport = "local";
      hostPlatform = "linux";
      hostName = "linux-example";
      storageDirectory = "/home/example/nixberry-vms";
      toolPath = "VBoxManage";
      bridge = "enp1s0";
    };
  };
  virtualbox-windows-example = {
    backend = "virtualbox";
    connection = {
      transport = "ssh";
      hostPlatform = "windows";
      hostName = "WINDOWS-EXAMPLE";
      sshHost = "192.168.50.121";
      sshUser = "example";
      pythonCommand = "python";
      storageDirectory = "C:\\nixberry-vms";
      toolPath = "C:\\Program Files\\Oracle\\VirtualBox\\VBoxManage.exe";
      bridge = "Intel(R) Ethernet Connection";
    };
  };
  vmware-linux-example = {
    backend = "vmware";
    connection = {
      transport = "ssh";
      hostPlatform = "linux";
      hostName = "workstation-example";
      sshHost = "192.168.50.122";
      sshUser = "example";
      pythonCommand = "python3";
      storageDirectory = "/home/example/nixberry-vms";
      toolPath = "vmrun";
    };
  };
  vmware-windows-example = {
    backend = "vmware";
    connection = {
      transport = "ssh";
      hostPlatform = "windows";
      hostName = "WINDOWS-EXAMPLE";
      sshHost = "192.168.50.123";
      sshUser = "example";
      pythonCommand = "python";
      storageDirectory = "C:\\nixberry-vms";
      toolPath = "C:\\Program Files (x86)\\VMware\\VMware Workstation\\vmrun.exe";
    };
  };
}
