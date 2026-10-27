# Named physical NICs are validated without evaluating or building a NixOS system.
{ network }:
let
  addresses = import ../../modules/system/networking/addresses.nix { };
  lower =
    value: builtins.replaceStrings [ "A" "B" "C" "D" "E" "F" ] [ "a" "b" "c" "d" "e" "f" ] value;
  distinct =
    values:
    builtins.length values == builtins.length (
      builtins.attrNames (
        builtins.listToAttrs (
          map (value: {
            name = value;
            value = true;
          }) values
        )
      )
    );
  allowed = keys: value: builtins.all (key: builtins.elem key keys) (builtins.attrNames value);
  interfaces = network.interfaces or { };
  primary = network.primary or "";
  recoveryInterface = network.recoveryInterface or primary;
  dns = network.dns or [ ];
  validDns =
    values:
    builtins.isList values
    && builtins.all (value: builtins.isString value && addresses.ip value) values;
  valid =
    name: value:
    builtins.match "[a-z][a-z0-9-]{0,14}" name != null
    && name != "lo"
    && builtins.isAttrs value
    && allowed [ "mac" "mode" "address" "gateway" "dns" ] value
    && builtins.isString (value.mac or null)
    && builtins.match "([0-9a-f]{2}:){5}[0-9a-f]{2}" (lower value.mac) != null
    &&
      builtins.match "[0-9a-f][02468ace].*" (builtins.replaceStrings [ ":" ] [ "" ] (lower value.mac))
      != null
    && lower value.mac != "00:00:00:00:00:00"
    && (value.mode or "static") == "static"
    && builtins.isString (value.address or null)
    && addresses.ipv4WithPrefix value.address
    && builtins.isString (value.gateway or "")
    && ((value.gateway or "") == "" || addresses.ipv4 value.gateway)
    && validDns (value.dns or [ ]);
  rows = builtins.attrValues interfaces;
in
assert
  builtins.isAttrs network && allowed [ "primary" "recoveryInterface" "dns" "interfaces" ] network
  || throw "RPi network requires primary, dns and named interfaces without other fields.";
assert
  builtins.isAttrs interfaces
  && builtins.length (builtins.attrNames interfaces) >= 1
  && builtins.length (builtins.attrNames interfaces) <= 8
  && builtins.isString primary
  && builtins.hasAttr primary interfaces
  || throw "RPi network.primary must select one of 1–8 named interfaces.";
assert
  builtins.isString recoveryInterface && builtins.hasAttr recoveryInterface interfaces
  || throw "RPi network.recoveryInterface must select a declared bootstrap adapter.";
assert
  builtins.all (name: valid name interfaces.${name}) (builtins.attrNames interfaces)
  || throw "Named RPi interfaces require safe names, unicast MACs and static IPv4 addresses; DHCP is not supported.";
assert
  distinct (map (value: lower value.mac) rows)
  && distinct (map (value: builtins.head (builtins.split "/" value.address)) rows)
  || throw "Named RPi interfaces require distinct MACs and IPv4 addresses.";
assert
  builtins.length (builtins.filter (value: (value.gateway or "") != "") rows) <= 1
  || throw "Named RPi interfaces support at most one default gateway.";
assert
  validDns dns && dns != [ ]
  || throw "RPi network.dns requires a nonempty list of IPv4 or IPv6 addresses.";
{
  inherit primary recoveryInterface dns;
  interfaces = builtins.mapAttrs (_: value: {
    mac = lower value.mac;
    mode = "static";
    inherit (value) address;
    gateway = value.gateway or "";
    dns = value.dns or (if (value.gateway or "") != "" then dns else [ ]);
  }) interfaces;
}
