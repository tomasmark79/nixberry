# Controller policy is declaration data; private key contents are never imported.
backup:
let
  allowed = [ "enable" "provider" "repository" "identityFile" "recipient" "restoreBackends" "schedule" "keepLast" "monitor" "afterUpdate" ];
  monitor = backup.monitor or { };
  monitorDefaults = { enable = false; maxAgeHours = 36; intervalMinutes = 15; maxRunMinutes = 90; notify = true; repeatHours = 24; };
  bounded = name: maximum: builtins.isInt normalized.monitor.${name}
    && normalized.monitor.${name} >= 1 && normalized.monitor.${name} <= maximum;
  absolute = value: builtins.isString value && builtins.match "/[^[:cntrl:]]*" value != null
    && !(builtins.substring 0 11 value == "/nix/store/");
  enabled = backup.enable or false;
  normalized = {
    enable = enabled;
    afterUpdate = backup.afterUpdate or false;
    provider = backup.provider or null;
    repository = backup.repository or null;
    identityFile = backup.identityFile or null;
    recipient = backup.recipient or null;
    restoreBackends = backup.restoreBackends or [ ];
    schedule = backup.schedule or null;
    keepLast = backup.keepLast or null;
    monitor = monitorDefaults // monitor;
  };
in
assert builtins.isAttrs backup && builtins.all (key: builtins.elem key allowed) (builtins.attrNames backup)
  || throw "backup accepts enable, provider, repository, identityFile, recipient, restoreBackends, schedule, keepLast, monitor and afterUpdate.";
assert builtins.isBool enabled || throw "backup.enable must be a boolean.";
assert builtins.isBool normalized.afterUpdate || throw "backup.afterUpdate must be a boolean.";
assert builtins.isAttrs monitor && builtins.all (key: builtins.hasAttr key monitorDefaults) (builtins.attrNames monitor)
  && builtins.isBool normalized.monitor.enable && builtins.isBool normalized.monitor.notify
  && bounded "maxAgeHours" 8760 && bounded "intervalMinutes" 1440
  && bounded "maxRunMinutes" 10080 && bounded "repeatHours" 8760
  || throw "backup.monitor requires known fields, booleans and positive bounded age, interval, runtime and reminder values.";
assert !enabled || (
  builtins.isString normalized.provider
  && builtins.match "configurations(/[A-Za-z0-9_-][A-Za-z0-9_.-]*)+\\.py" normalized.provider != null
  && absolute normalized.repository && absolute normalized.identityFile
  && builtins.isString normalized.recipient && builtins.match "age1[0-9a-z]{58}" normalized.recipient != null
  && builtins.isList normalized.restoreBackends
  && builtins.all (value: builtins.elem value [ "libvirt" "hyperv" "proxmox" "virtualbox" "vmware" "rpi" ]) normalized.restoreBackends
  && (normalized.schedule == null || (builtins.isString normalized.schedule && builtins.match "[^[:cntrl:]]+" normalized.schedule != null))
  && (normalized.keepLast == null || (builtins.isInt normalized.keepLast && normalized.keepLast >= 2))
) || throw "Enabled backup requires a configuration provider, absolute runtime repository/identity paths outside /nix/store, a public age recipient, supported restoreBackends, optional calendar and keepLast >= 2.";
normalized
