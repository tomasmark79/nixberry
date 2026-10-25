# Validate explicit adapter declarations before systems or hypervisors are used.
{ backend, spec }:
let
  fields = {
    libvirt = [ "network" ];
    hyperv = [ "switchName" "switchId" ];
    proxmox = [ "bridge" ];
    virtualbox = [ "bridge" ];
    vmware = [ "vmnet" ];
  }.${backend};
  keys = [ "name" "mac" ] ++ fields;
  values = spec.networkAdapters or [ ];
  normalize = value: builtins.replaceStrings [ ":" "-" "A" "B" "C" "D" "E" "F" ]
    [ "" "" "a" "b" "c" "d" "e" "f" ] value;
  distinct = values: builtins.length values == builtins.length (builtins.attrNames
    (builtins.listToAttrs (map (value: { name = value; value = true; }) values)));
  valid = adapter:
    builtins.isAttrs adapter
    && builtins.attrNames adapter == builtins.attrNames (builtins.listToAttrs (map
      (key: { name = key; value = true; }) keys))
    && builtins.all (key: builtins.isString adapter.${key} && adapter.${key} != "") keys
    && builtins.match "[a-z][a-z0-9-]{0,14}" adapter.name != null
    && adapter.name != "lo"
    && builtins.match (if backend == "hyperv" then "([0-9a-fA-F]{2}){6}"
      else "([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}") adapter.mac != null
    && builtins.match "[0-9a-f][02468ace][0-9a-f]{10}" (normalize adapter.mac) != null
    && normalize adapter.mac != "000000000000"
    && (backend != "libvirt" || builtins.match "52:54:00(:[0-9a-f]{2}){3}" adapter.mac != null)
    && (backend != "vmware" || builtins.match "00:50:56:[0-3][0-9a-fA-F](:[0-9a-fA-F]{2}){2}" adapter.mac != null)
    && builtins.all (key: builtins.match "[^[:cntrl:]]+" adapter.${key} != null) fields
    && (backend != "libvirt" || builtins.match "[A-Za-z0-9][A-Za-z0-9_.-]*" adapter.network != null)
    && (backend != "proxmox" || builtins.match "[A-Za-z0-9][A-Za-z0-9_.-]*" adapter.bridge != null)
    && (backend != "vmware" || adapter.vmnet == "nat" || builtins.match "vmnet([0-9]|1[0-9])" adapter.vmnet != null)
    && (backend != "hyperv" || builtins.match "[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}" adapter.switchId != null);
in
assert !(spec ? networkAdapters) || (
  builtins.isList values && builtins.length values >= 1 && builtins.length values <= 8
  && builtins.all valid values
  && distinct (map (adapter: adapter.name) values)
  && distinct (map (adapter: normalize adapter.mac) values)
  && normalize (builtins.head values).mac == normalize spec.mac
  && builtins.all (key: (builtins.head values).${key} == (spec.${key} or
    (if backend == "vmware" then "nat" else null))) fields
) || throw "networkAdapters requires 1–8 unique named adapters; the first must match the primary MAC and attachment.";
spec
