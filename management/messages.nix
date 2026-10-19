# One shared source for management tools and installation environments.
{ pkgs }:
import ./with-locales.nix {
  inherit pkgs;
  source = pkgs.lib.fileset.toSource {
    root = ./deployment;
    fileset = pkgs.lib.fileset.unions [
      ./deployment/messages.py
      ./deployment/messages.sh
      ./deployment/shell_runner.py
      ./deployment/locales/cs.sh
    ];
  };
}
