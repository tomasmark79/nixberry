# Jeden společný zdroj pro ovládací nástroje i instalační prostředí.
{ pkgs }:
pkgs.lib.fileset.toSource {
  root = ./deployment;
  fileset = pkgs.lib.fileset.unions [
    ./deployment/messages.py
    ./deployment/messages.sh
    ./deployment/shell_runner.py
    (pkgs.lib.fileset.fileFilter (file: file.hasExt "mo") ./deployment/locales)
  ];
}
