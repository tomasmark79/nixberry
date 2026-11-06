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

    def test_shared_management_file_aliases_are_materialized_and_sealed_as_regular_files(self):
        original = self.root / 'management/original.py'
        original.write_text('shared source')
        (self.root / 'management/messages.py').symlink_to('original.py')
        payload = self.archive()
        self.module.validate(payload)
        with tarfile.open(fileobj=io.BytesIO(payload), mode='r:') as archive:
            alias = archive.getmember('recipe/management/messages.py')
            self.assertTrue(alias.isfile())
            self.assertEqual(archive.extractfile(alias).read(), b'shared source')

    def test_management_aliases_cannot_escape_or_import_runtime_credentials(self):
        (self.base / 'outside.py').write_text('outside secret')
        alias = self.root / 'management/alias.py'
        for target in (self.base / 'outside.py', self.root / 'management', self.root / 'password.key'):
            with self.subTest(target=target):
                alias.symlink_to(target)
                with self.assertRaises(ValueError):
                    self.archive()
                alias.unlink()

    def test_selection_refuses_symbolic_links_inside_the_selected_configuration(self):
        (self.root / 'configurations/server/unrelated').symlink_to(self.base)
        with self.assertRaisesRegex(ValueError, 'symbolic'):
            self.archive()

    def test_imported_configuration_sources_keep_secret_and_symlink_guards(self):
        imported = self.root / 'configurations/imported'
        imported.mkdir()
        for name in ('password.key', 'secrets', 'alias'):
            with self.subTest(name=name):
                private = imported / name
                if name == 'alias':
                    private.symlink_to(self.base)
                else:
                    private.write_text('private runtime data')
                with self.assertRaises(ValueError):
                    with self.module.selected_recipe(self.root, 'vm-demo-source', self.target, {}, sources=['imported']):
                        self.fail('Unsafe imported source was accepted')
                private.unlink()

    def test_imported_source_selectors_cannot_copy_parents_or_ignored_metadata(self):
        (self.root / 'configurations/.git').mkdir()
        (self.root / 'configurations/.git/config').write_text('private repository metadata')
        for name in ('', '.', '..', '../outside', '/outside', 'server/default.nix', '.git'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                with self.module.selected_recipe(self.root, 'vm-demo-source', self.target, {}, sources=[name]):
                    self.fail('Unsafe source selector was accepted')

    def test_guest_archive_validation_does_not_require_controller_recipe_modules(self):
        import shutil
        import subprocess
        import sys
        guest = self.base / 'guest'
        guest.mkdir()
        for name in ('recovery_recipe.py', 'messages.py'):
            shutil.copyfile(Path(__file__).with_name(name), guest / name)
        archive = guest / 'recovery.tar'
        archive.write_bytes(self.archive())
        result = subprocess.check_output([sys.executable, '-I', '-c',
            'import json,pathlib,sys; sys.path.insert(0,sys.argv[1]); import recovery_recipe; '
            'print(json.dumps(recovery_recipe.validate(pathlib.Path(sys.argv[2]).read_bytes())))',
            str(guest), str(archive)], text=True)
        self.assertEqual(json.loads(result)['target'], 'vm-demo-source')

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
    def test_imported_linked_role_reproduces_after_source_checkout_is_removed(self):
        import shutil
        import subprocess
        from native_controller import prepare_archive, projection
        from recipe_source import materialized_recipe
        from scaffold import new_vm
        from test_support import copy_test_recipe
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / 'recipe'
            external = base / 'external'
            other = base / 'unselected'
            copy_test_recipe(Path(__file__).resolve().parents[2], root)
            for directory in ('roles/recovery', 'services/assets'):
                (external / directory).mkdir(parents=True)
            (external / 'default.nix').write_text('''{ nixberryModules, ... }: {
              imports = [ nixberryModules.common ./services ];
              nixberry.backup = { enable = true; restic = {
                enable = true; repository = "/var/lib/test-repository";
              }; };
            }''')
            (external / 'roles/recovery/default.nix').write_text('{ imports = [ ../../default.nix ]; }')
            (external / 'settings.nix').write_text('{ path = "/var/lib/recovery-demo"; }')
            (external / 'services/default.nix').write_text('''{ ... }: {
              nixberry.backup.applications.web.paths = [ (import ../settings.nix).path ];
              environment.etc."recovery-demo.txt".source = ./assets/example.txt;
            }''')
            (external / 'services/assets/example.txt').write_text('selected role asset\n')
            other.mkdir()
            (other / 'default.nix').write_text('throw "Unselected linked configuration must not be evaluated"')
            (other / 'password.key').write_text('unrelated private runtime credential')
            (root / 'configurations/workload').symlink_to(external, target_is_directory=True)
            (root / 'configurations/unselected').symlink_to(other, target_is_directory=True)
            (root / 'configurations/role.nix').write_text('{ imports = [ ./workload/roles/recovery ]; }')
            (root / 'configurations/private.nix').write_text('throw "Unselected configuration must not be evaluated"')
            new_vm(root, {}, {}, 'vm-demo-source', 'libvirt', offline=True, configuration='role', filesystem='btrfs')
            new_vm(root, {}, {}, 'vm-demo-other', 'hyperv', offline=True)
            with materialized_recipe(root) as materialized:
                target = json.loads(subprocess.check_output(['nix', 'eval', '--json', '--no-update-lock-file',
                    'path:' + materialized + '#nixberryDeployment.targets.vm-demo-source'], text=True))
                original = projection(materialized, 'vm-demo-source')
                payload, manifest = prepare_archive(materialized, 'vm-demo-source', target, original, base)
            self.assertIn('configurations/workload/settings.nix', manifest['files'])
            self.assertIn('configurations/workload/services/assets/example.txt', manifest['files'])
            self.assertNotIn('configurations/unselected/default.nix', manifest['files'])
            self.assertNotIn('configurations/private.nix', manifest['files'])
            self.assertNotIn('machines/vm/vm-demo-other.nix', manifest['files'])
            shutil.rmtree(root)
            shutil.rmtree(external)
            shutil.rmtree(other)
            recovered = base / 'recovered'
            import recovery_recipe
            recovery_recipe.unpack(payload, recovered)
            self.assertEqual(projection(str(recovered / 'recipe'), 'vm-demo-source'), original)

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
