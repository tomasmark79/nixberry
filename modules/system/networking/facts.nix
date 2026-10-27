# A service-facing view; platform modules own its values and NIC configuration.
{ lib, ... }:
{
  options.nixberry.network = {
    primaryInterface = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      readOnly = true;
      description = "Declared primary management interface, or null when the platform discovers it dynamically.";
    };
    networkdUnits = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      readOnly = true;
      description = "Interface to existing networkd unit key; service modules may extend that unit without replacing its NIC match.";
    };
    interfaces = lib.mkOption {
      readOnly = true;
      description = "Effective declared NIC identities and addressing, shared by VM and physical platforms.";
      type = lib.types.attrsOf (
        lib.types.submodule {
          options = {
            name = lib.mkOption { type = lib.types.str; };
            mac = lib.mkOption { type = lib.types.str; };
            mode = lib.mkOption {
              type = lib.types.enum [
                "disabled"
                "dhcp"
                "static"
              ];
            };
            address = lib.mkOption { type = lib.types.str; };
            gateway = lib.mkOption { type = lib.types.str; };
            dns = lib.mkOption { type = lib.types.listOf lib.types.str; };
          };
        }
      );
    };
  };
}
