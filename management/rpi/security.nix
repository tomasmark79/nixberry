# Physical-board policy contains only public settings and a pinned public key.
{
  security ? { },
}:
let
  mode = security.mode or "none";
  unlock = security.unlock or "manual";
  port = security.port or 2222;
  initrdHostKey = security.initrdHostKey or null;
  recoveryHostKey = security.recoveryHostKey or null;
  interface = security.interface or null;
  address = security.address or null;
  addresses = import ../../modules/system/networking/addresses.nix { };
in
assert builtins.isAttrs security || throw "RPi security must be an attribute set.";
assert
  builtins.all (
    key:
    builtins.elem key [
      "mode"
      "unlock"
      "port"
      "initrdHostKey"
      "recoveryHostKey"
      "interface"
      "address"
    ]
  ) (builtins.attrNames security)
  || throw "RPi security contains unsupported fields; never put secrets in declarations.";
assert
  builtins.elem mode [
    "none"
    "luks"
  ]
  || throw "RPi security.mode must be none or luks.";
assert unlock == "manual" || throw "RPi security.unlock must be manual.";
assert
  builtins.isInt port && port > 0 && port < 65536 && (mode == "none" || port != 22)
  || throw "RPi unlock SSH port must be valid and different from ordinary SSH (22).";
assert
  initrdHostKey == null
  || (
    builtins.isString initrdHostKey
    && builtins.match "ssh-ed25519 [A-Za-z0-9+/]+=*( [^\n\r]*)?" initrdHostKey != null
  )
  || throw "RPi security.initrdHostKey must be a public Ed25519 SSH key.";
assert
  recoveryHostKey == null
  || (
    builtins.isString recoveryHostKey
    && builtins.match "ssh-ed25519 [A-Za-z0-9+/]+=*( [^\n\r]*)?" recoveryHostKey != null
  )
  || throw "RPi security.recoveryHostKey must be a public Ed25519 SSH key.";
assert
  interface == null
  || (
    builtins.isString interface
    && interface != "lo"
    && builtins.match "[a-z][a-z0-9-]{0,14}" interface != null
  )
  || throw "RPi security.interface must name a declared static adapter.";
assert
  address == null
  || (builtins.isString address && addresses.ipv4WithPrefix address)
  || throw "RPi security.address must be a valid IPv4 address with a prefix length.";
{
  inherit
    mode
    unlock
    port
    initrdHostKey
    recoveryHostKey
    ;
}
// (if interface == null then { } else { inherit interface; })
// (if address == null then { } else { inherit address; })
