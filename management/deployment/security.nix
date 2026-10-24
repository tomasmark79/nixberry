# Public policy only. Secret material is supplied at runtime, outside the store.
{ security ? { } }:
let
  mode = security.mode or "none";
  unlock = security.unlock or (if mode == "confidential" then "attested" else "manual");
  port = security.port or 2222;
  imageDirectory = security.imageDirectory or null;
in
assert builtins.isAttrs security || throw "VM security must be an attribute set.";
assert builtins.all (key: builtins.elem key [ "mode" "unlock" "port" "imageDirectory" ]) (builtins.attrNames security)
  || throw "VM security contains unsupported fields; never put secrets in declarations.";
assert builtins.elem mode [ "none" "luks" "confidential" ]
  || throw "VM security.mode must be none, luks or confidential.";
assert unlock == (if mode == "confidential" then "attested" else "manual")
  || throw "VM security.unlock must be manual for LUKS or attested for confidential VMs.";
assert builtins.isInt port && port > 0 && port < 65536
  || throw "VM security.port must be between 1 and 65535.";
assert mode == "none" || port != 22
  || throw "Early-boot SSH must use a different port from ordinary SSH (22).";
assert imageDirectory == null || (builtins.isString imageDirectory && builtins.substring 0 1 imageDirectory == "/"
  && !(builtins.match ".*[\n\r].*" imageDirectory != null)
  && !(builtins.substring 0 11 imageDirectory == "/nix/store/"))
  || throw "VM security.imageDirectory must be an absolute runtime path outside the Nix store.";
{ inherit mode unlock port imageDirectory; }
