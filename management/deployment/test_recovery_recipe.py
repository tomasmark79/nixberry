"""Recovery recipes include selected local declarations without unrelated private data."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch


class RecoveryRecipeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('recovery_recipe'), 'Selected recovery archives are not implemented')
        import recovery_recipe
        self.module = recovery_recipe
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'frozen'
        for directory in ('management', 'modules', 'platforms', 'configurations/server', 'machines/vm', 'machines/rpi'):
            (self.root / directory).mkdir(parents=True)
        for name, text in {'flake.nix':'{}', 'flake.lock':'{}', 'hosts.nix':'import ./hosts.local.nix',
            'admin.pub':'public key', 'modules/example.nix':'{}', 'configurations/default.nix':'{}',
            'configurations/server/default.nix':'{}', 'machines/vm/default.nix':'{}', 'machines/rpi/default.nix':'{}',
            'machines/vm/vm-demo-source.nix':'{ configuration="server"; }',
            'machines/rpi/rpi-other.nix':'private other machine', 'configurations/private.nix':'private other configuration',
            'hosts.local.nix':'private unrelated hosts', 'password.key':'private runtime credential'}.items():
            (self.root / name).write_text(text)
        self.target = {'backend':'libvirt', 'offline':True, 'configuration':'server'}
        self.metadata = {'format':'nixberry-recovery-v1', 'sourceId':'libvirt:example', 'target':'vm-demo-source',
            'systemPath':'/nix/store/'+'a'*32+'-nixos-system-vm-demo-source', 'policySha256':'b'*64}

    def archive(self):
        destination = self.base / 'recovery.tar'
        with self.module.selected_recipe(self.root, 'vm-demo-source', self.target, {}) as recipe:
            self.module.pack(recipe, self.metadata, destination)
        contents = destination.read_bytes()
        destination.unlink()
        return contents

    def test_selected_archive_excludes_other_declarations_configs_hosts_and_runtime_files(self):
        payload = self.archive()
        metadata = self.module.validate(payload)
        with tarfile.open(fileobj=io.BytesIO(payload), mode='r:') as archive:
            names = {entry.name for entry in archive.getmembers()}
            self.assertIn('recipe/configurations/server/default.nix', names)
            self.assertIn('recipe/machines/vm/vm-demo-source.nix', names)
            self.assertNotIn('recipe/machines/rpi/rpi-other.nix', names)
            self.assertNotIn('recipe/configurations/private.nix', names)
            self.assertNotIn('recipe/configurations/default.nix', names)
            self.assertNotIn('recipe/password.key', names)
            self.assertEqual(archive.extractfile('recipe/hosts.local.nix').read().decode(), 'builtins.fromJSON "{}"\n')
        self.assertEqual(metadata['sourceId'], 'libvirt:example')
        self.assertEqual(metadata['files']['modules/example.nix'], hashlib.sha256(b'{}').hexdigest())

    def test_only_the_selected_host_is_encoded_without_nix_interpolation(self):
        import subprocess
        target = {**self.target, 'offline': False, 'host': 'chosen'}
        hosts = {'chosen': {'backend': 'libvirt', 'connection': {'uri': 'qemu:///system'}, 'note': '${literal}'},
                 'unrelated': {'note': 'private unrelated host'}}
        with self.module.selected_recipe(self.root, 'vm-demo-source', target, hosts) as recipe:
            data = json.loads(subprocess.check_output(['nix','eval','--impure','--json','--expr',
                'import '+str(Path(recipe)/'hosts.local.nix')], text=True))
        self.assertEqual(data, {'chosen': {'backend': 'libvirt', 'connection': {'uri': 'qemu:///system'}, 'note': '${literal}'}})

    def test_archive_creation_cannot_overwrite_an_existing_file(self):
        destination = self.base / 'recovery.tar'
        destination.write_bytes(b'keep existing file')
        with self.assertRaises(FileExistsError):
            self.archive()
        self.assertEqual(destination.read_bytes(), b'keep existing file')

    def test_machine_local_flake_inputs_are_not_accepted_as_independent_recovery(self):
        (self.root / 'flake.lock').write_text(json.dumps({'nodes': {'private': {'locked': {'type': 'path', 'path': '/home/example/private-input'}}}}))
        with self.assertRaisesRegex(ValueError, 'portable'):
            self.archive()

    def test_selection_refuses_symbolic_links_inside_the_selected_configuration(self):
        (self.root / 'configurations/server/unrelated').symlink_to(self.base)
        with self.assertRaisesRegex(ValueError, 'symbolic'):
            self.archive()

    def test_tampered_source_file_cannot_pass_manifest_validation(self):
        payload = self.archive()
        output = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(payload), mode='r:') as source, tarfile.open(fileobj=output, mode='w') as result:
            for item in source:
                contents = source.extractfile(item).read() if item.isfile() else None
                if item.name == 'recipe/modules/example.nix':
                    contents = b'changed'
                    item.size = len(contents)
                result.addfile(item, io.BytesIO(contents) if contents is not None else None)
        with self.assertRaisesRegex(ValueError, 'manifest'):
            self.module.validate(output.getvalue())

    def test_duplicate_and_traversal_archive_members_are_rejected(self):
        for filename in ('../escape', '/etc/passwd', 'recipe/../escape', 'recovery.json'):
            payload = self.archive()
            output = io.BytesIO()
            with tarfile.open(fileobj=io.BytesIO(payload), mode='r:') as source, tarfile.open(fileobj=output, mode='w') as result:
                for item in source:
                    result.addfile(item, source.extractfile(item) if item.isfile() else None)
                item = tarfile.TarInfo(filename)
                item.size = 1
                result.addfile(item, io.BytesIO(b'x'))
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                self.module.validate(output.getvalue())

    def test_file_cannot_be_a_parent_of_another_archive_entry(self):
        payload = self.archive()
        output = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(payload), mode='r:') as source, tarfile.open(fileobj=output, mode='w') as result:
            for item in source:
                result.addfile(item, source.extractfile(item) if item.isfile() else None)
            child = tarfile.TarInfo('recipe/flake.nix/escape')
            child.size = 1
            result.addfile(child, io.BytesIO(b'x'))
        with self.assertRaisesRegex(ValueError, 'Conflicting file'):
            self.module.validate(output.getvalue())

    def test_failed_extraction_retains_a_visible_incomplete_directory_and_cannot_overwrite_it(self):
        destination = self.base / 'recovered'
        data = self.archive()
        with patch('recovery_recipe.shutil.copyfileobj', side_effect=OSError('simulated disk failure')):
            with self.assertRaises(OSError):
                self.module.unpack(data, destination)
        self.assertTrue((destination / 'INCOMPLETE').is_file())
        with self.assertRaises(FileExistsError):
            self.module.unpack(data, destination)

    def test_declared_logical_file_size_is_bounded_before_payload_reads(self):
        entry = tarfile.TarInfo('recipe/oversized')
        entry.size = self.module.MAX_ARCHIVE + 1
        data = entry.tobuf() + b'\0' * 1024
        with self.assertRaisesRegex(ValueError, 'size limit'):
            self.module.validate(data)


class RecoveryProjectionTests(unittest.TestCase):
    def test_selected_recipe_reproduces_its_configuration_without_other_targets(self):
        import subprocess
        from native_controller import projection
        from recovery_recipe import selected_recipe
        from scaffold import new_vm
        from test_support import copy_test_recipe
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'recipe'
            copy_test_recipe(Path(__file__).resolve().parents[2], root)
            (root / 'configurations/server.nix').write_text('{ ... }: {\n              imports=[ ../modules/common.nix ];\n              nixberry.backup={ enable=true; applications.web.paths=["/var/lib/web"];\n                restic={ enable=true; repository="/var/lib/test-repository"; }; };\n            }')
            (root / 'configurations/private.nix').write_text('throw "Unselected configuration must not be evaluated"')
            new_vm(root, {}, {}, 'vm-demo-source', 'libvirt', offline=True, configuration='server', filesystem='btrfs')
            new_vm(root, {}, {}, 'vm-demo-other', 'hyperv', offline=True)
            target = json.loads(subprocess.check_output(['nix','eval','--json','--no-write-lock-file',
                'path:'+str(root)+'#nixberryDeployment.targets.vm-demo-source'], text=True))
            original = projection(str(root), 'vm-demo-source')
            with selected_recipe(root, 'vm-demo-source', target, {}) as selected:
                recovered = projection(selected, 'vm-demo-source')
                self.assertEqual(recovered, original)
                declared = json.loads(subprocess.check_output(['nix','eval','--json','--no-write-lock-file',
                    'path:'+selected+'#nixberryDeployment.targets'], text=True))
                self.assertEqual(list(declared), ['vm-demo-source'])
            text = (root / 'examples/rpi.nix').read_text().rstrip()
            (root / 'machines/rpi/rpi-demo-source.nix').write_text(text[:-1] + 'configuration="server"; }')
            target = json.loads(subprocess.check_output(['nix','eval','--json','--no-write-lock-file',
                'path:'+str(root)+'#nixberryDeployment.targets.rpi-demo-source'], text=True))
            original = projection(str(root), 'rpi-demo-source')
            with selected_recipe(root, 'rpi-demo-source', target, {}) as selected:
                self.assertEqual(projection(selected, 'rpi-demo-source'), original)
