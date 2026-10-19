# Available operations depend only on capabilities, not systems or media.
target:
let
  optional = condition: values: if condition then values else [ ];
  caps = target.capabilities;
in
[
  "show"
  "export"
]
++ optional caps.system [
  "status"
  "doctor"
  "ssh"
  "verify"
  "update"
  "diff"
  "diffg"
]
++ optional caps.hypervisor [
  "probe"
  "create"
  "install"
  "provision"
  "reset"
]
++ optional (caps.hypervisor || target.backend == "rpi") [
  "cleanup"
  "reproduce"
  "plan"
]
++ optional (target.backend == "rpi") [ "probe" ]
