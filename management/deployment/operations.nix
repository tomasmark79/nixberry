# Available operations depend only on capabilities, not systems or media.
target:
let
  optional = condition: values: if condition then values else [ ];
  caps = target.capabilities;
  backup = target.backup.enable or false;
in
[
  "show"
  "export"
  "backups"
  "verify-backup"
  "recover-source"
  "backup-check"
]
++ optional ((target.security.mode or "none") != "none") (
  [ "unlock" ] ++ optional (target.backend != "rpi") [ "prepare-image" ]
)
++ optional caps.system [
  "status"
  "doctor"
  "ssh"
  "verify"
  "update"
  "diff"
  "diffg"
  "restore"
  "snapshot"
  "snapshots"
  "snapshot-remove"
  "snapshot-resume"
  "backup-prune"
]
++ optional backup [
  "backup-schedule"
  "backup-monitor"
]
++ optional (backup && !caps.system) [ "backup-prune" ]
++ optional caps.system [
  "backup"
  "backup-stage"
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
