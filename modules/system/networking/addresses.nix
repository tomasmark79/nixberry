{ lib }:
let
  ipv4 =
    address:
    let
      octets = lib.splitString "." address;
    in
    builtins.length octets == 4
    && builtins.all (
      octet: builtins.match "(0|[1-9][0-9]{0,2})" octet != null && lib.toInt octet <= 255
    ) octets;
  ipv4WithPrefix =
    address:
    let
      parts = lib.splitString "/" address;
    in
    builtins.length parts == 2
    && ipv4 (builtins.head parts)
    && builtins.match "(0|[1-9][0-9]?)" (builtins.elemAt parts 1) != null
    && lib.toInt (builtins.elemAt parts 1) <= 32;
  ipv6 =
    address:
    let
      halves = lib.splitString "::" address;
      groups = lib.concatMap (half: if half == "" then [ ] else lib.splitString ":" half) halves;
      last = if groups == [ ] then "" else lib.last groups;
      mapped = lib.hasInfix "." last;
      hexadecimal = if mapped then lib.init groups else groups;
      count = builtins.length groups + (if mapped then 1 else 0);
    in
    builtins.length halves <= 2
    && builtins.all (group: builtins.match "[0-9A-Fa-f]{1,4}" group != null) hexadecimal
    && (!mapped || (ipv4 last && lib.hasSuffix last address))
    && (if builtins.length halves == 2 then count < 8 else count == 8);
in
{
  inherit ipv4 ipv4WithPrefix;
  ip = address: ipv4 address || ipv6 address;
}
