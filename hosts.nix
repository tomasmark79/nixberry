# Místní připojení: zkopíruj vybrané šablony z management/examples/hosts.nix
# do hosts.local.nix. Čistá kopie projektu nemá žádné aktivní hostitele.
if builtins.pathExists ./hosts.local.nix then import ./hosts.local.nix else { }
