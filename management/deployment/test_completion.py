"""Základní doplňování bez nástrojů; schopnosti z místního Nixu, bez sítě a buildů."""

import ast
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class CompletionTests(unittest.TestCase):
    def test_backup_options_source_selection_and_local_snapshot_completion(self):
        repository = Path(self.tmp.name) / 'private-backups'; repository.mkdir(mode=0o700)
        identity = 'b' * 32
        directory = repository / ('snapshot-' + identity); directory.mkdir(mode=0o700)
        (directory / 'receipt.json').write_text(json.dumps({'target': 'kozlonix', 'snapshotId': identity, 'verification': {'status': 'ok'}}))
        declaration = self.root / 'instances/kozlonix.nix'
        declaration.write_text('{ host="local"; spec.name="kozlonix"; backup={'
            'enable=true; provider="configurations/server/provider.py"; repository=' + json.dumps(str(repository)) + ';'
            'identityFile="/private/key"; recipient="age1' + 'a'*58 + '"; restoreBackends=["libvirt"]; }; }')
        self.assertEqual(self.complete('restore', 'kozlonix', '--from', 'koz'), ['kozlonix'])
        self.assertEqual(self.complete('verify-backup', 'kozlonix', '--snapshot', 'b'), [identity])
        self.assertEqual(self.complete('restore', 'other', '--from', 'kozlonix', '--snapshot', 'b'), [identity])
        self.assertIn('--apply', self.complete('backup', 'kozlonix', '--'))
        self.assertNotIn('--apply', self.complete('backups', 'kozlonix', '--'))
        self.assertIn('--output', self.complete('backup-schedule', 'kozlonix', '--'))
        self.assertIn('--apply', self.complete('backup-check', 'kozlonix', '--'))
        self.assertIn('--apply', self.complete('backup-stage', 'kozlonix', '--'))
        self.assertIn('--output', self.complete('backup-monitor', 'kozlonix', '--'))
        self.assertNotIn('--apply', self.complete('backup-monitor', 'kozlonix', '--'))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "recept s mezerou"
        for directory in ("instances", "management/rpi/targets"):
            (self.root / directory).mkdir(parents=True)
            shutil.copyfile(Path(__file__).resolve().parents[2] / directory / "default.nix",
                            self.root / directory / "default.nix")
        (self.root / "instances/kozlonix.nix").write_text('{ host = "local"; spec.name = "kozlonix"; }')
        (self.root / "management/rpi/targets/nixberry.nix").write_text('{ hostName = "pi"; }')
        (self.root / "instances/adresar.nix").mkdir()
        (self.root / "instances/link.nix").symlink_to("kozlonix.nix")
        self.recipe = Path(__file__).resolve().parents[2]
        shutil.copyfile(self.recipe / "completion.bash", self.root / "completion.bash")
        (self.root / 'management/deployment').mkdir(parents=True)
        for name in ('security.nix', 'normalize-target.nix', 'network.nix'):
            shutil.copyfile(self.recipe / 'management/rpi' / name, self.root / 'management/rpi' / name)
        for name in ('default.nix', 'security.nix', 'vm-network.nix', 'configurations.nix', 'operations.nix', 'backup-policy.nix', 'completion-targets.nix', 'completion-snapshots.nix', 'shell-path.sh', 'messages.sh'):
            shutil.copyfile(Path(__file__).with_name(name), self.root / 'management/deployment' / name)
        (self.root / 'configurations').mkdir()
        (self.root / 'flake.nix').write_text('{}')
        (self.root / 'configurations/default.nix').write_text('{}')
        (self.root / 'hosts.nix').write_text('{ local = { backend = "libvirt"; connection.uri = "qemu:///system"; }; }')
        self.tools = Path(self.tmp.name) / 'tools'
        self.tools.mkdir()
        (self.tools / 'nix').symlink_to(shutil.which('nix'))
        self.bash = shutil.which("bash")

    def complete(self, *words, before="", nix=True, command="nixberry"):
        script = '''
source "$1/completion.bash"
complete -p nixberry >/dev/null || exit 1
eval "$2"
shift 2
COMP_WORDS=("$COMPLETION_COMMAND" "$@")
COMP_CWORD=$((${#COMP_WORDS[@]} - 1))
if [[ $COMPLETION_COMMAND == nixberry ]]; then _nixberry_complete; else _nixberry_complete_shortcut; fi
if ((${#COMPREPLY[@]})); then printf '%s\\n' "${COMPREPLY[@]}"; fi
'''
        result = subprocess.run(
            [self.bash, "--noprofile", "--norc", "-c", script, "completion-test",
             str(self.root), before, *words],
            cwd=self.tmp.name, env={"PATH": str(self.tools) if nix else "", "COMPLETION_COMMAND": command}, text=True,
            capture_output=True, check=True,
        )
        self.assertEqual(result.stderr, "")
        return result.stdout.splitlines()

    def test_commands_match_cli(self):
        tree = ast.parse((Path(__file__).with_name("cli.py")).read_text())
        choices = next(
            ast.literal_eval(keyword.value)
            for node in ast.walk(tree) if isinstance(node, ast.Call)
            for keyword in node.keywords if keyword.arg == "choices"
        )
        self.assertEqual(self.complete(""), list(choices))
        self.assertEqual(self.complete("ver"), ["verify", "verify-backup"])

    def test_targets_and_prefix(self):
        self.assertEqual(self.complete("verify", ""), ["kozlonix", "nixberry"])
        self.assertEqual(self.complete("update", "koz"), ["kozlonix"])

    def test_reset_never_offers_physical_rpi(self):
        self.assertEqual(self.complete("reset", ""), ["kozlonix"])

    def test_offline_capabilities_for_every_backend_and_method(self):
        for backend in ('libvirt', 'hyperv', 'proxmox', 'virtualbox', 'vmware'):
            path = self.root / 'instances/demo.nix'
            for method in ('image', 'iso'):
                for access in ('', 'access = { host = "10.0.1.19"; user = "root"; port = 22; };'):
                    path.write_text('{ backend = "' + backend + '"; deploymentMethod = "' + method + '"; '
                                    + access + ' spec = { name = "demo"; memoryMiB = throw "Doplňování nesmí číst kapacitu VM"; }; }')
                    with self.subTest(backend=backend, method=method, access=bool(access)):
                        for action in ('create', 'install', 'provision', 'reset', 'probe', 'plan', 'reproduce', 'cleanup'):
                            self.assertEqual(self.complete(action, 'dem'), [])
                        for action in ('ssh', 'verify', 'update', 'diff', 'diffg'):
                            self.assertEqual(self.complete(action, 'dem'), ['demo'] if access else [])
                            self.assertEqual(self.complete(action, '--ssh', 'root@10.0.1.19', 'dem'), ['demo'])
                        self.assertEqual(self.complete('show', 'dem'), ['demo'])
                        self.assertEqual(self.complete('export', 'dem'), ['demo'])

    def test_online_capabilities_for_every_backend_and_method(self):
        for backend in ('libvirt', 'hyperv', 'proxmox', 'virtualbox', 'vmware'):
            (self.root / 'hosts.nix').write_text('{ local = { backend = "' + backend + '"; connection = {}; }; }')
            for method in ('image', 'iso'):
                (self.root / 'instances/demo.nix').write_text('{ host = "local"; deploymentMethod = "' + method + '"; spec.name = "demo"; }')
                with self.subTest(backend=backend, method=method):
                    for action in ('probe', 'create', 'install', 'provision', 'reset', 'plan', 'reproduce', 'cleanup', 'verify', 'update'):
                        self.assertEqual(self.complete(action, 'dem'), ['demo'])

    def test_no_nix_or_invalid_declaration_does_not_guess_capabilities(self):
        self.assertEqual(self.complete('show', 'koz', nix=False), ['kozlonix'])
        self.assertEqual(self.complete('update', 'koz', nix=False), [])
        (self.root / 'instances/kozlonix.nix').write_text('not valid nix')
        self.assertEqual(self.complete('reset', 'koz'), [])

    def test_changed_host_is_reflected_in_same_shell_without_cache(self):
        self.assertEqual(self.complete('reset', 'koz', before='''
COMP_WORDS=(nixberry reset koz); COMP_CWORD=2; _nixberry_complete
[[ ${COMPREPLY[*]} == kozlonix ]] || exit 1
printf '%s' '{ backend = "proxmox"; spec.name = "kozlonix"; }' > "$1/instances/kozlonix.nix"
'''), [])

    def test_deployment_methods_complete_values_and_preserve_target_parsing(self):
        self.assertEqual(self.complete("plan", "kozlonix", "--method", ""), ["iso", "image"])
        self.assertEqual(self.complete("plan", "--method", "iso", "koz"), ["kozlonix"])
        self.assertIn("--method", self.complete("new-proxmox-vm", "nova", "--"))
        self.assertNotIn("--method", self.complete("plan", "nixberry", "--"))

    def test_configurations_complete_without_nix_and_preserve_target_parsing(self):
        (self.root / "configurations/web").mkdir(parents=True)
        (self.root / "configurations/default.nix").touch()
        (self.root / "configurations/web/default.nix").touch()
        (self.root / "configurations/link.nix").symlink_to("default.nix")
        (self.root / "configurations/linked").symlink_to("web", target_is_directory=True)
        self.assertEqual(self.complete("new-libvirt-vm", "demo", "--configuration", ""), ["default", "linked", "web"])
        self.assertEqual(self.complete("new-proxmox-vm", "--configuration", "web", "--"),
                         self.complete("new-proxmox-vm", "--configuration", "web", "demo", "--"))
        self.assertNotIn("--configuration", self.complete("update", "kozlonix", "--"))
        self.assertIn("--configuration", self.complete("new-proxmox-vm", "demo", "--"))

    def test_hosts_complete_for_selected_backend_without_tools(self):
        hosts = self.root / "hosts.nix"
        hosts.write_text('''{
  local-libvirt = {
    backend = "libvirt";
    connection = { uri = "qemu:///system"; };
  };
  pvelab = { backend = "proxmox"; };
  dalsi-libvirt = {
    backend = "libvirt";
  };
  # neplatny = { backend = "libvirt"; };
}''')
        for suffix in ("", "-vm"):
            action = "new-libvirt" + suffix
            self.assertEqual(self.complete(action, "labik", "--method", "image", "--host", ""),
                             ["local-libvirt", "dalsi-libvirt"])
            self.assertEqual(self.complete(action, "labik", "--host", "local"), ["local-libvirt"])
        self.assertEqual(self.complete("new-proxmox-vm", "nova", "--host", ""), ["pvelab"])
        self.assertEqual(self.complete("new-vmware-vm", "nova", "--host", ""), [])
        self.assertEqual(self.complete("update", "kozlonix", "--host", ""), [])
        hosts.unlink()
        self.assertEqual(self.complete("new-libvirt-vm", "labik", "--host", ""), [])

    def test_local_hosts_override_loader_and_follow_edits_without_nix(self):
        shutil.copyfile(self.recipe / 'hosts.nix', self.root / 'hosts.nix')
        self.assertEqual(self.complete('new-proxmox-vm', 'demo', '--host', '', nix=False), [])
        local = self.root / 'hosts.local.nix'
        local.write_text('{\n pve-demo = { backend = "proxmox"; };\n}')
        self.assertEqual(self.complete('new-proxmox-vm', 'demo', '--host', '', nix=False), ['pve-demo'])
        local.write_text('{\n hv-demo = { backend = "hyperv"; };\n}')
        self.assertEqual(self.complete('new-proxmox-vm', 'demo', '--host', '', nix=False), [])
        self.assertEqual(self.complete('new-hyperv-vm', 'demo', '--host', '', nix=False), ['hv-demo'])

    def test_changes_are_visible_in_same_shell(self):
        self.assertEqual(self.complete("show", "nova", before='''
COMP_WORDS=(nixberry show nova); COMP_CWORD=2; _nixberry_complete
[[ ${#COMPREPLY[@]} == 0 ]] || exit 1
: > "$1/instances/nova.nix"
'''), ["nova"])

    def test_recipe_override_and_empty_inventory(self):
        self.assertEqual(self.complete("show", "", before='NIXBERRY_RECIPE="$1/instances"'), [])

    def test_invalid_recipe_clears_previous_completions(self):
        for command, words in (("nixberry", ("show", "")), ("nixrebuildberry", ("",))):
            with self.subTest(command=command):
                self.assertEqual(self.complete(*words, command=command, before='''
COMP_WORDS=(nixberry sh); COMP_CWORD=1; _nixberry_complete
[[ ${COMPREPLY[*]} == show ]] || exit 1
NIXBERRY_RECIPE=/missing-nixberry-recipe
'''), [])

    def test_source_by_filename(self):
        result = subprocess.run(
            [self.bash, "--noprofile", "--norc", "-c",
             'source completion.bash; COMP_WORDS=(nixberry show koz); COMP_CWORD=2; '
             '_nixberry_complete; printf "%s\\n" "${COMPREPLY[@]}"'],
            cwd=self.root, env={"PATH": ""}, text=True, capture_output=True, check=True,
        )
        self.assertEqual(result.stdout, "kozlonix\n")
        self.assertEqual(result.stderr, "")

    def test_shortcuts_delegate_target_and_option_completion(self):
        for shortcut in ('nvdiffberry', 'nvdiffberryg', 'nixrebuildberry', 'nixberryedit'):
            with self.subTest(shortcut=shortcut):
                self.assertEqual(self.complete('koz', command=shortcut), ['kozlonix'])
        options = self.complete('kozlonix', '--', command='nixrebuildberry')
        self.assertIn('--ssh', options)
        self.assertNotIn('--apply', options)
        self.assertNotIn('--dry-run', options)

    def test_nixon_checkout_is_independent_and_explicit_recipe_has_priority(self):
        self.assertEqual(self.complete('show', 'koz', before='NIXON_CONFIG_DIR="$1/.."'), ['kozlonix'])
        self.assertEqual(self.complete('show', 'koz', before='NIXBERRY_RECIPE=/missing'), [])
        self.assertEqual(self.complete('show', 'koz', before='NIXON_CONFIG_DIR=/missing; NIXBERRY_RECIPE="$1"'), ['kozlonix'])

    def test_new_commands_do_not_offer_existing_names(self):
        for backend in ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware"):
            for suffix in ("", "-vm"):
                action = "new-" + backend + suffix
                self.assertEqual(self.complete(action, ""), [])
                self.assertIn("--host", self.complete(action, "nova", "--"))
                self.assertIn("--offline", self.complete(action, "nova", "--"))

    def test_options_and_consumed_target(self):
        self.assertEqual(self.complete("--json", "ver"), ["verify", "verify-backup"])
        self.assertEqual(self.complete("--host", "example", "verify", "koz"), ["kozlonix"])
        self.assertEqual(self.complete("verify", "--ssh=example", "koz"), ["kozlonix"])
        self.assertEqual(self.complete("verify", "--ssh", ""), [])
        self.assertEqual(self.complete("verify", "kozlonix", ""), [])
        self.assertEqual(self.complete("ssh", "kozlonix", "--", ""), [])
        self.assertEqual(self.complete("--"), ["--help", "--version"])

    def test_option_prefixes_after_target(self):
        self.assertEqual(self.complete("update", "kozlonix", "--a"), ["--apply"])
        self.assertEqual(self.complete("export", "kozlonix", "--o"), ["--output"])
        self.assertEqual(self.complete("new-proxmox", "nova", "--h"), ["--help", "--host"])
        self.assertEqual(self.complete("cleanup", "--artifact"), ["--artifact-root"])

    def test_options_are_specific_to_command(self):
        for action in ("new-libvirt", "new-hyperv", "new-proxmox", "new-virtualbox", "new-vmware"):
            options = self.complete(action, "nova", "--")
            self.assertIn("--host", options)
            self.assertIn("--dry-run", options)
            for invalid in ("--apply", "--json", "--ssh", "--output"):
                self.assertNotIn(invalid, options)
        self.assertNotIn("--apply", self.complete("diffg", "kozlonix", "--"))
        self.assertNotIn("--dry-run", self.complete("ssh", "kozlonix", "--"))
        self.assertEqual(self.complete("list", "--"), ["--help", "--version", "--json"])

    def test_rpi_recovery_options(self):
        for action in ("plan", "reproduce"):
            options = self.complete(action, "nixberry", "--")
            self.assertIn("--address", options)
            self.assertIn("--erase-existing", options)
            self.assertNotIn("--address", self.complete(action, "kozlonix", "--"))

    def test_used_and_conflicting_options(self):
        for action in ("new-libvirt", "new-hyperv", "new-proxmox", "new-virtualbox", "new-vmware"):
            self.assertIn("--offline", self.complete(action, "nova", "--"))
            self.assertNotIn("--host", self.complete(action, "nova", "--offline", "--"))
            self.assertNotIn("--offline", self.complete(action, "nova", "--host", "hostitel", "--"))
        self.assertNotIn("--offline", self.complete("show", "kozlonix", "--"))
        self.assertNotIn("--apply", self.complete("update", "kozlonix", "--dry-run", "--"))
        options = self.complete("update", "kozlonix", "--apply", "--")
        self.assertNotIn("--apply", options)
        self.assertNotIn("--dry-run", options)
        self.assertNotIn("--ssh", self.complete("verify", "--host=example", "--"))
        self.assertNotIn("--host", self.complete("verify", "--ssh", "example", "--"))
        self.assertIn("--artifact-root", self.complete("cleanup", "--artifact-root", "/tmp", "--"))
        self.assertEqual(self.complete("export", "kozlonix", "--output", "--"), [])
        self.assertEqual(self.complete("ssh", "kozlonix", "--", "--"), [])


if __name__ == "__main__":
    unittest.main()
