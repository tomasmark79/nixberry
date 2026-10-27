{
  lib ? null,
}:
let
  # Keep validation usable before nixpkgs or a complete NixOS system is evaluated.
  split =
    separator: value:
    builtins.filter builtins.isString (
      builtins.split (builtins.replaceStrings [ "." ] [ "\\." ] separator) value
    );
  integer = builtins.fromJSON;
  ipv4 =
    address:
    let
      octets = split "." address;
    in
    builtins.length octets == 4
    && builtins.all (
      octet: builtins.match "(0|[1-9][0-9]{0,2})" octet != null && integer octet <= 255
    ) octets;
  ipv4WithPrefix =
    address:
    let
      parts = split "/" address;
    in
    builtins.length parts == 2
    && ipv4 (builtins.head parts)
    && builtins.match "(0|[1-9][0-9]?)" (builtins.elemAt parts 1) != null
    && integer (builtins.elemAt parts 1) <= 32;
  sameIPv4Subnet =
    first: second:
    let
      parts = value: split "/" value;
      prefix = value: integer (builtins.elemAt (parts value) 1);
      number =
        value:
        builtins.foldl' (total: octet: total * 256 + integer octet) 0 (
          split "." (builtins.head (parts value))
        );
      power = exponent: builtins.foldl' (total: _: total * 2) 1 (builtins.genList (_: null) exponent);
      mask = 4294967296 - power (32 - prefix first);
    in
    ipv4WithPrefix first
    && ipv4WithPrefix second
    && prefix first == prefix second
    && builtins.bitAnd (number first) mask == builtins.bitAnd (number second) mask;
  ipv6 =
    address:
    let
      halves = split "::" address;
      groups = builtins.concatLists (map (half: if half == "" then [ ] else split ":" half) halves);
      last = if groups == [ ] then "" else builtins.elemAt groups (builtins.length groups - 1);
      mapped = builtins.length (split "." last) > 1;
      hexadecimal =
        if mapped then
          builtins.genList (i: builtins.elemAt groups i) (builtins.length groups - 1)
        else
          groups;
      count = builtins.length groups + (if mapped then 1 else 0);
    in
    builtins.length halves <= 2
    && builtins.all (group: builtins.match "[0-9A-Fa-f]{1,4}" group != null) hexadecimal
    && (
      !mapped
      || (
        ipv4 last
        &&
          builtins.substring (
            builtins.stringLength address - builtins.stringLength last
          ) (builtins.stringLength last) address == last
      )
    )
    && (if builtins.length halves == 2 then count < 8 else count == 8);
in
{
  inherit ipv4 ipv4WithPrefix sameIPv4Subnet;
  ip = address: ipv4 address || ipv6 address;
}
