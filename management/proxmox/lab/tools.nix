let
  recipe = builtins.getFlake ("path:" + toString ../../..);
  pkgs = import recipe.inputs.nixpkgs { system = builtins.currentSystem; };
in
pkgs.symlinkJoin {
  name = "nixberry-proxmox-lab-tools";
  paths = with pkgs; [
    proxmox-auto-install-assistant
    xorriso
    qemu
    libvirt
    virt-manager
    openssl
    python3
  ];
}
