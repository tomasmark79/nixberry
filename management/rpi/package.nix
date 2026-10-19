{
  pkgs,
  targets,
  recoveries,
  guestJq,
}:
assert guestJq.system == "aarch64-linux";
let
  commonSource = import ../messages.nix { inherit pkgs; };
  manifest = pkgs.writeText "nixberry-rpi-targets.json" (builtins.toJSON targets);
  recoverySpec = pkgs.writeText "nixberry-rpi-recovery.json" (builtins.toJSON recoveries);
  install = pkgs.writeShellApplication {
    name = "nixberry-rpi-install";
    runtimeInputs = with pkgs; [
      python3
      openssh
      nix
    ];
    text = ''
      export NIXBERRY_RPI_TARGETS=${manifest}
      export NIXBERRY_RPI_PROBE=${./probe.sh}
      export NIXBERRY_RPI_GUEST=${./guest-install.sh}
      export NIXBERRY_RPI_JQ_STORE=${guestJq}
      export PYTHONPATH=${commonSource}
      exec python3 ${./install.py} "$@"
    '';
  };
  recoveryTest = pkgs.writeShellApplication {
    name = "nixberry-rpi-recovery-test";
    runtimeInputs = with pkgs; [
      python3
      openssh
      nix
    ];
    text = ''
      export NIXBERRY_RPI_TARGETS=${manifest}
      export NIXBERRY_RPI_PROBE=${./probe.sh}
      export NIXBERRY_RPI_MODULE=${./install.py}
      export NIXBERRY_RECOVERY_SPEC=${recoverySpec}
      export PYTHONPATH=${commonSource}
      exec python3 ${./recovery-test.py} "$@"
    '';
  };
in
pkgs.symlinkJoin {
  name = "nixberry-rpi-tools";
  paths = [
    install
    recoveryTest
  ];
}
