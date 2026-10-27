# Keep the flat runtime projection for existing physical-board tooling.
target:
let
  security = import ./security.nix { security = target.security or { }; };
in
if !(target ? network) then
  assert
    (security.interface or null) == null
    || throw "RPi security.interface requires a named static network.";
  assert
    (security.address or null) == null || throw "RPi security.address requires a named static network.";
  target
else
  let
    network = import ./network.nix { inherit (target) network; };
    primary = network.interfaces.${network.primary};
    early = if (security.interface or null) == null then network.primary else security.interface;
    addresses = import ../../modules/system/networking/addresses.nix { };
  in
  assert
    builtins.all (key: !(builtins.hasAttr key target)) [
      "address"
      "gateway"
      "dns"
      "lanMac"
      "reserveMac"
    ]
    || throw "Named RPi network cannot be mixed with flat address, gateway, dns, lanMac or reserveMac fields.";
  assert
    builtins.hasAttr early network.interfaces
    || throw "RPi security.interface must select an existing named static interface.";
  assert
    (security.address or null) == null
    || addresses.sameIPv4Subnet security.address network.interfaces.${early}.address
    || throw "RPi security.address must preserve the selected interface's IPv4 subnet and prefix.";
  target
  // {
    inherit network;
    address = primary.address;
    gateway = primary.gateway;
    dns = network.dns;
    lanMac = primary.mac;
    reserveMac = null;
  }
