"""Izolovaný recept pro testy; nečte místní identity, připojení ani klíč správce."""

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
        return excluded

    shutil.copytree(recipe, destination, ignore=ignore)
    # Veřejný testovací klíč s nulovými daty; žádný soukromý klíč ani živé nasazení.
    (Path(destination) / 'admin.pub').write_text(
        'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA test-only\n')
