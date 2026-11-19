{ config, lib, ... }:
{
  time.timeZone = "Europe/Prague";
  i18n.defaultLocale = "cs_CZ.UTF-8";

  # Avoid a NixOS setup-etc.pl race where /etc/zoneinfo is briefly unlinked
  # while PID 1 recalculates OnCalendar timers with an explicit timezone.
  # /etc/static is swapped atomically, so /etc/static/zoneinfo stays readable
  # throughout switch-to-configuration.
  systemd.managerEnvironment.TZDIR = lib.mkIf (!config.system.etc.overlay.enable) (
    lib.mkForce "/etc/static/zoneinfo"
  );
}
