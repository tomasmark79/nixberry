# A gamepad-operated console; hardware and networking remain in the platform/target.
{ ... }:
{
  imports = [
    ../modules/common.nix
    ../modules/applications/retrogaming
  ];
}
