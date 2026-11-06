"""Select, seal and validate source recipes for independent application recovery."""
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile

from messages import _

FORMAT = 'nixberry-recovery-v1'
MAX_ARCHIVE = 128 * 1024 * 1024
MAX_FILES = 20000
PUBLIC = ('flake.nix', 'flake.lock', 'VERSION', 'LICENSE', 'README.md', 'AGENTS.md',
          '.gitignore', 'nixberry', 'completion.bash', 'hosts.nix', 'assets', 'management',
          'modules', 'platforms', 'docs', 'examples', 'tests')
IGNORE = shutil.ignore_patterns('.git', '__pycache__', '.cache', '.pytest_cache', '.ruff_cache',
                               'result', 'result-*', '*.pyc', '*.mo', '*.log', '*~', '*.swp')
HASH = re.compile('[0-9a-f]{64}')
NAME = re.compile('[A-Za-z0-9][A-Za-z0-9_-]*')
SYSTEM = re.compile('/nix/store/[a-z0-9]{32}-nixos-system-[A-Za-z0-9+._-]+')


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n'


def copy_source(source, destination, *, alias_root=None):
    if source.is_symlink():
        # Repository management tools share regular Python files through local
        # aliases. Seal their bytes, never links or anything outside this tree.
        if alias_root is not None:
            resolved = source.resolve(strict=True)
            if resolved.is_file() and resolved.is_relative_to(alias_root) and 'secrets' not in resolved.relative_to(alias_root).parts:
                return copy_source(resolved, destination)
        raise ValueError(_("Recovery recipe source must not contain symbolic links."))
    if source.name == 'secrets' or source.suffix in ('.key', '.secret'):
        raise ValueError(_("Keep private runtime credentials outside recovery recipe sources."))
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        entries = sorted(source.iterdir())
        ignored = set(IGNORE(str(source), [entry.name for entry in entries]))
        for entry in entries:
            if entry.name not in ignored:
                copy_source(entry, destination / entry.name, alias_root=alias_root)
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(source, destination)
        destination.chmod(0o755 if source.stat().st_mode & 0o111 else 0o644)
    else:
        raise ValueError(_("Recovery recipes require regular source files and directories."))


@contextmanager
def selected_recipe(frozen, name, target, hosts):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError(_("Invalid recovery recipe target name."))
    configuration = target.get('configuration', 'default')
    if not isinstance(configuration, str) or not re.fullmatch('[a-z][a-z0-9-]*', configuration):
        raise ValueError(_("Invalid selected recovery configuration."))
    frozen = Path(frozen)
    with tempfile.TemporaryDirectory(prefix='nixberry-recovery-') as temporary:
        root = Path(temporary) / 'recipe'
        root.mkdir(mode=0o700)
        for entry in PUBLIC:
            source = frozen / entry
            if source.exists() or source.is_symlink():
                copy_source(source, root / entry, alias_root=source if entry == 'management' else None)
        for kind in ('vm', 'rpi'):
            copy_source(frozen / 'machines' / kind / 'default.nix', root / 'machines' / kind / 'default.nix')
        copy_source(frozen / 'admin.pub', root / 'admin.pub')
        kind = 'rpi' if target['backend'] == 'rpi' else 'vm'
        relative = Path('machines') / kind / (name + '.nix')
        copy_source(frozen / relative, root / relative)
        for selected in (configuration,):
            candidates = [path for path in (frozen / 'configurations' / (selected + '.nix'),
                          frozen / 'configurations' / selected) if path.exists() or path.is_symlink()]
            if len(candidates) != 1:
                raise ValueError(_("Recovery requires one regular source for each selected configuration."))
            source = candidates[0]
            copy_source(source, root / 'configurations' / source.name)
        host = target.get('host') if target['backend'] != 'rpi' and not target.get('offline') else None
        selected_hosts = {host: hosts[host]} if host is not None else {}
        # Encode JSON as one quoted Nix string; host values cannot interpolate Nix.
        encoded = json.dumps(json.dumps(selected_hosts, ensure_ascii=False), ensure_ascii=False).replace('${', r'\${')
        (root / 'hosts.local.nix').write_text('builtins.fromJSON ' + encoded + '\n')
        yield str(root)


def pack(recipe, metadata, destination):
    recipe = Path(recipe)
    entries = sorted(recipe.rglob('*'))
    if len(entries) > MAX_FILES or sum(path.stat().st_size for path in entries if path.is_file()) > MAX_ARCHIVE:
        raise ValueError(_("Recovery recipe archive exceeds its size limit."))
    files = {str(path.relative_to(recipe)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in entries if path.is_file() and not path.is_symlink()}
    if any(path.is_symlink() or not (path.is_file() or path.is_dir()) for path in entries):
        raise ValueError(_("Recovery recipes require regular source files and directories."))
    record = {**metadata, 'files': files}
    contents = encode(record).encode()
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream, tarfile.open(fileobj=stream, mode='w', format=tarfile.PAX_FORMAT) as archive:
        header = tarfile.TarInfo('recovery.json')
        header.size = len(contents)
        header.mode = 0o600
        archive.addfile(header, io.BytesIO(contents))
        def normalize(header):
            header.uid = header.gid = header.mtime = 0
            header.uname = header.gname = ''
            header.mode = 0o700 if header.isdir() else (0o755 if header.mode & 0o111 else 0o644)
            return header
        archive.add(recipe, arcname='recipe', filter=normalize)
    with Path(destination).open('rb') as stream:
        data = stream.read(MAX_ARCHIVE + 1)
    validate(data)
    return hashlib.sha256(data).hexdigest()


def member_name(value):
    path = PurePosixPath(value)
    if (not value or len(value) > 4096 or path.is_absolute() or str(path) != value
            or '..' in path.parts or any(ord(character) < 32 or ord(character) == 127 for character in value)):
        raise ValueError(_("Unsafe path in recovery recipe archive."))
    return path


def portable_lock(contents):
    lock = json.loads(contents)
    if not isinstance(lock, dict) or not isinstance(lock.get('nodes', {}), dict):
        raise ValueError(_("Recovery requires portable pinned flake inputs."))
    for node in lock.get('nodes', {}).values():
        locked = node.get('locked', {}) if isinstance(node, dict) else None
        if (not isinstance(locked, dict)
                or locked.get('type') == 'path' and (not isinstance(locked.get('path'), str)
                    or PurePosixPath(locked['path']).is_absolute())
                or isinstance(locked.get('url'), str) and locked['url'].startswith(('file:', '/'))):
            raise ValueError(_("Recovery requires portable pinned flake inputs."))


def validate(data):
    if not isinstance(data, bytes) or len(data) > MAX_ARCHIVE:
        raise ValueError(_("Recovery recipe archive exceeds its size limit."))
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:') as archive:
            names, files, record, kinds, total = set(), {}, None, {}, 0
            for count, item in enumerate(archive):
                path = member_name(item.name)
                if (count > MAX_FILES or item.name in names or item.type not in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE)
                        or item.mode & 0o7000 or item.sparse is not None or item.isdir() and item.size != 0):
                    raise ValueError(_("Unsafe or duplicate entry in recovery recipe archive."))
                total += item.size
                if item.size < 0 or total > MAX_ARCHIVE:
                    raise ValueError(_("Recovery recipe archive exceeds its size limit."))
                names.add(item.name)
                kinds[path] = item.isdir()
                if item.name == 'recovery.json' and item.isfile() and item.size <= 4 * 1024 * 1024:
                    record = json.load(archive.extractfile(item))
                elif path.parts[0] == 'recipe':
                    if item.isfile():
                        source = archive.extractfile(item)
                        if str(path.relative_to('recipe')) == 'flake.lock':
                            if item.size > 4 * 1024 * 1024:
                                raise ValueError(_("Recovery requires portable pinned flake inputs."))
                            portable_lock(source.read())
                            source.seek(0)
                        files[str(path.relative_to('recipe'))] = hashlib.file_digest(source, 'sha256').hexdigest()
                else:
                    raise ValueError(_("Unsafe path in recovery recipe archive."))
            if any(parent in kinds and not kinds[parent] for path in kinds for parent in path.parents):
                raise ValueError(_("Conflicting file and directory paths in recovery recipe archive."))
            required = {'format', 'sourceId', 'target', 'systemPath', 'policySha256', 'files'}
            if (not isinstance(record, dict) or set(record) != required or record['format'] != FORMAT
                    or not isinstance(record['sourceId'], str) or not re.fullmatch('[A-Za-z0-9:_.-]{1,128}', record['sourceId'])
                    or not isinstance(record['target'], str) or not NAME.fullmatch(record['target'])
                    or not isinstance(record['systemPath'], str) or not SYSTEM.fullmatch(record['systemPath'])
                    or not isinstance(record['policySha256'], str) or not HASH.fullmatch(record['policySha256'])
                    or record['files'] != files or not {'flake.nix', 'flake.lock', 'admin.pub'}.issubset(files)):
                raise ValueError(_("Recovery recipe archive does not match its sealed file manifest."))
            return record
    except (tarfile.TarError, UnicodeError, json.JSONDecodeError) as failure:
        raise ValueError(_("Invalid recovery recipe archive.")) from failure


def unpack(data, destination):
    """Write validated code into a new directory; never evaluate its contents."""
    record = validate(data)
    destination = Path(destination)
    destination.mkdir(mode=0o700)
    incomplete = destination / 'INCOMPLETE'
    with incomplete.open('x') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write('Recovery recipe extraction has not completed.\n')
        stream.flush()
        os.fsync(stream.fileno())
    def sync(path):
        descriptor = os.open(path, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    sync(destination)
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:') as archive:
        for item in archive:
            path = destination / item.name
            if item.isdir():
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
            else:
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, 'wb') as stream:
                    shutil.copyfileobj(archive.extractfile(item), stream)
                    stream.flush()
                    os.fchmod(stream.fileno(), 0o755 if item.mode & 0o111 else 0o600)
                    os.fsync(stream.fileno())
    for path in sorted((path for path in destination.rglob('*') if path.is_dir()), key=lambda path: len(path.parts), reverse=True):
        sync(path)
    incomplete.unlink()
    sync(destination)
    return record
