# Local connections: copy selected templates from management/examples/hosts.nix
# into hosts.local.nix. A clean checkout has no active hosts.
if builtins.pathExists ./hosts.local.nix then import ./hosts.local.nix else { }
