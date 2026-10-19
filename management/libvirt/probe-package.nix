# Shared CLI and direct-tool discovery without building media.
{ pkgs, spec }:
pkgs.writeShellApplication {
  name = "nixberry-libvirt-probe";
  runtimeInputs = [ pkgs.libvirt ];
  text = ''
    export LC_ALL=C
    virsh --connect ${pkgs.lib.escapeShellArg spec.uri} list --all
    virsh --connect ${pkgs.lib.escapeShellArg spec.uri} pool-info ${pkgs.lib.escapeShellArg spec.storagePool}
    virsh --connect ${pkgs.lib.escapeShellArg spec.uri} net-info ${pkgs.lib.escapeShellArg spec.network}
    names=$(virsh --connect ${pkgs.lib.escapeShellArg spec.uri} list --all --name)
    while IFS= read -r name; do
      if [[ "$name" == ${pkgs.lib.escapeShellArg spec.name} ]]; then
        virsh --connect ${pkgs.lib.escapeShellArg spec.uri} dominfo "$name"
        virsh --connect ${pkgs.lib.escapeShellArg spec.uri} domifaddr "$name" --source lease
      fi
    done <<< "$names"
  '';
}
