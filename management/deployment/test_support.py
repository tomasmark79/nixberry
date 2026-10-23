"""Isolated recipe fixtures exclude local identities, connections and keys."""

from pathlib import Path
import shutil


def copy_test_recipe(recipe, destination):
    recipe = Path(recipe)
    declarations = {recipe / 'instances', recipe / 'management/rpi/targets'}

    def ignore(directory, names):
        excluded = set(shutil.ignore_patterns(
            '.git', '__pycache__', 'result*', 'hosts.local.nix', 'admin.pub', 'secrets')(directory, names))
        if Path(directory) in declarations:
            excluded.update(name for name in names if name.endswith('.nix') and name != 'default.nix')
        if Path(directory) == recipe / 'configurations':
            excluded.update(name for name in names if (Path(directory) / name).is_symlink())
        return excluded

    shutil.copytree(recipe, destination, ignore=ignore)
    # Public key with zero-filled test data; no private key or live deployment.
    (Path(destination) / 'admin.pub').write_text(
        'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA test-only\n')
