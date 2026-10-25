"""Add runtime initrd-secret appending to the pinned upstream RPi loader."""

from pathlib import Path
import re
import sys


def patch_loader(source, destination):
    """Keep upstream generation promotion and fail if its expected shape changes."""
    source, destination = Path(source), Path(destination)
    parent = source.read_text()
    children = set(re.findall(r"/nix/store/[a-z0-9]{32}-kernelboot-gen-builder\.sh", parent))
    if len(children) != 1:
        raise ValueError("The pinned RPi bootloader has an unexpected generation builder.")
    child = Path(children.pop())
    generation = child.read_text()
    needle = '    copyForced "$initrd" "$genDir/initrd"\n'
    if generation.count(needle) != 1:
        raise ValueError("The pinned RPi generation builder has an unexpected initrd copy operation.")
    hook = '''    if [ -x "$generationPath/append-initrd-secrets" ]; then
        "$generationPath/append-initrd-secrets" "$genDir/initrd"
    fi
'''
    destination.mkdir()
    new_child = destination / child.name
    new_child.write_text(generation.replace(needle, needle + hook))
    new_child.chmod(0o755)
    new_parent = destination / source.name
    new_parent.write_text(parent.replace(str(child), str(new_child)))
    new_parent.chmod(0o755)


if __name__ == "__main__":
    patch_loader(*sys.argv[1:])
