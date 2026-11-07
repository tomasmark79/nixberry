# Exercise device metadata and real nested mounts without host privileges.
{ pkgs }:
let
  tools = import ../management/deployment/capture-package.nix { inherit pkgs; };
  fixtures = pkgs.lib.fileset.toSource {
    root = ../management/deployment;
    fileset = pkgs.lib.fileset.unions [
      ../management/deployment/test_restore_apply.py
      ../management/deployment/test_snapshots.py
      ../management/deployment/restore_privileged.py
    ];
  };
in
pkgs.testers.runNixOSTest {
  name = "nixberry-restore-publication";
  nodes.machine = { ... }: {
    virtualisation.memorySize = 1024;
    environment.systemPackages = [ pkgs.python3 pkgs.rsync pkgs.util-linux ];
  };
  testScript = ''
    import shlex

    machine.start()
    machine.wait_for_unit("multi-user.target")
    check = "import unittest; suite = unittest.defaultTestLoader.loadTestsFromName('restore_privileged'); result = unittest.TextTestRunner(verbosity=2).run(suite); raise SystemExit(0 if result.wasSuccessful() and result.testsRun and not result.skipped else 1)"
    machine.succeed("env NIXBERRY_LANG=en PYTHONPATH=${fixtures}:${tools.source} python3 -c " + shlex.quote(check))
  '';
}
