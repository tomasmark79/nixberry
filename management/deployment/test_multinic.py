"""Ordered NIC creation and identity checks without contacting a hypervisor."""

import copy
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import host_agent
import runtime
from messages import python_script
from vm_network import adapters, guest_checks, libvirt_matches
from check_inventory import evaluate, nix
from test_support import copy_test_recipe


BACKENDS = ("libvirt", "hyperv", "proxmox", "virtualbox", "vmware")


def spec(backend):
    result = {"mac": "00:50:56:12:34:01", "name": "vm-demo-gateway",
              "uuid": "12345678-1234-4321-8123-123456789abc",
              "ownerId": "nixberry-12345678-1234-4321-8123-123456789abc",
              "memoryMiB": 4096, "vcpus": 2, "diskGiB": 32}
    attachments = {
        "libvirt": ({"network": "vm-demo-wan"}, {"network": "vm-demo-lan"}),
        "hyperv": ({"switchName": "WAN", "switchId": "12345678-1234-4321-8123-123456789abc"},
                   {"switchName": "LAN", "switchId": "12345678-1234-4321-8123-123456789abd"}),
        "proxmox": ({"bridge": "vmbr0"}, {"bridge": "vmbr1"}),
        "virtualbox": ({"bridge": "WAN adapter"}, {"bridge": "LAN adapter"}),
        "vmware": ({"vmnet": "nat"}, {"vmnet": "vmnet2"}),
    }[backend]
    if backend == "libvirt":
        result["mac"] = "52:54:00:12:34:01"
    if backend == "hyperv":
        result["mac"] = "025056123401"
    result.update(attachments[0])
    result["networkAdapters"] = [
        {"name": "wan0", "mac": result["mac"], **attachments[0]},
        {"name": "lan0", "mac": result["mac"][:-1] + "2", **attachments[1]},
    ]
    return result


class AdapterContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is unavailable")
    def test_hyperv_host_and_reset_check_every_switch_and_mac(self):
        value = spec('hyperv')
        for filename in ['host.ps1', 'reset.ps1']:
            source = (Path(__file__).resolve().parents[1] / 'hyperv' / filename).read_text()
            block = '#' + source.split('# The first declared adapter', 1)[1].split('$base = Canonical', 1)[0]
            # The reset script starts the block at a different position but ends at the same storage binding.
            for changed in [False, True]:
                actual = [{"MacAddress": adapter['mac'], "SwitchId": adapter['switchId']} for adapter in value['networkAdapters']]
                if changed:
                    actual[1]['SwitchId'] = value['switchId']
                script = '''$ErrorActionPreference='Stop'
function Require($condition, [string]$message) { if (-not $condition) { throw $message } }
function Get-VMSwitch { param($Name) foreach ($item in $s.networkAdapters) { if ($item.switchName -eq $Name) { [pscustomobject]@{Id=[Guid]$item.switchId} } } }
function Get-VMNetworkAdapter { param($VM) return $actual }
''' + '$s = ConvertFrom-Json -InputObject ' + "'" + json.dumps(value) + "'\n"
                script += '$actual = ConvertFrom-Json -InputObject ' + "'" + json.dumps(actual) + "'\n"
                script += block + '\nCheckAdapters $null\n'
                result = subprocess.run(['pwsh', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode == 0, not changed, filename + ': ' + result.stderr)

    def test_portable_preflight_rejects_a_secondary_mac_collision(self):
        for backend in ['virtualbox', 'proxmox', 'vmware']:
            value = spec(backend)
            with tempfile.TemporaryDirectory() as directory:
                value.update(storageDirectory=directory, toolPath='tool')
                second = value['networkAdapters'][1]['mac']
                if backend == 'virtualbox':
                    response = f'macaddress1="{second.replace(":", "")}"'
                    registry = patch.object(host_agent, 'vbox_registry', return_value=[{'uuid': 'other'}])
                elif backend == 'proxmox':
                    response = json.dumps({'net0': f'virtio={second},bridge=vmbr1'})
                    registry = patch.object(host_agent, 'proxmox_registry', return_value=[{'type': 'qemu', 'node': 'pve', 'vmid': 101}])
                else:
                    (Path(directory) / 'other.vmx').write_text(f'ethernet0.address = "{second}"\n')
                    response = 'Total running VMs: 0'
                    registry = patch.object(host_agent, 'proxmox_registry')
                with self.subTest(backend=backend), registry, patch.object(host_agent, 'run', return_value=response), self.assertRaisesRegex(RuntimeError, 'already used'):
                    host_agent.network_conflicts(value, backend)

    def test_primary_aliases_and_legacy_defaults_for_all_backends(self):
        for backend in BACKENDS:
            with self.subTest(backend=backend):
                value = spec(backend)
                self.assertEqual(adapters(value, backend), value["networkAdapters"])
                old = {key: item for key, item in value.items() if key != "networkAdapters"}
                self.assertEqual(adapters(old, backend)[0]["mac"], old["mac"])

    def test_ambiguous_or_changed_declarations_fail_before_host_access(self):
        for backend in BACKENDS:
            valid = spec(backend)
            invalid = [[], valid["networkAdapters"] * 5, None, "wan0"]
            for field, value in [("mac", valid["mac"]), ("name", "wan0"), ("name", "lo"),
                                 ("name", "../unsafe"), ("name", "UPPER"), ("unknown", True)]:
                candidate = copy.deepcopy(valid["networkAdapters"])
                candidate[1][field] = value
                invalid.append(candidate)
            invalid.append(list(reversed(valid["networkAdapters"])))
            for value in invalid:
                with self.subTest(backend=backend, value=value), self.assertRaises(ValueError):
                    adapters({**valid, "networkAdapters": value}, backend)

    def test_attachment_restrictions_apply_to_secondary_adapters(self):
        for backend, field, value in [("libvirt", "network", "../other"),
                                      ("hyperv", "switchId", "not-a-uuid"),
                                      ("proxmox", "bridge", "vmbr0,tag=10"),
                                      ("virtualbox", "bridge", "adapter\ncommand"),
                                      ("vmware", "vmnet", 'vmnet2"\nannotation="other')]:
            candidate = spec(backend)
            candidate["networkAdapters"][1][field] = value
            with self.subTest(backend=backend), self.assertRaises(ValueError):
                adapters(candidate, backend)

    def test_libvirt_checks_all_bindings_and_rejects_extra_devices(self):
        value = spec("libvirt")
        root = ET.fromstring('<domain><devices/></domain>')
        for adapter in reversed(value["networkAdapters"]):
            nic = ET.SubElement(root.find("devices"), "interface", {"type": "network"})
            ET.SubElement(nic, "mac", {"address": adapter["mac"]})
            ET.SubElement(nic, "source", {"network": adapter["network"]})
        self.assertTrue(libvirt_matches(root, value))
        wrong = copy.deepcopy(root)
        wrong.find("./devices/interface/source").set("network", "other")
        self.assertFalse(libvirt_matches(wrong, value))
        ET.SubElement(root.find("devices"), "interface", {"type": "bridge"})
        self.assertFalse(libvirt_matches(root, value))

    def test_hyperv_selects_primary_ips_and_verifies_secondary_switch_identity(self):
        value = {**spec("hyperv"), "windowsComputerName": "DEMO", "ownerId": "owner", "generation": 2, "sshHost": "10.0.0.1"}
        network = [{**item, "ips": [f"10.0.{i}.2"]} for i, item in enumerate(value["networkAdapters"])]
        network.reverse()
        data = {"computerName": "DEMO", "hypervModule": True, "vms": [{
            "name": value["name"], "ownerId": "owner", "generation": 2, "state": "Running", "network": network}]}
        target = {"spec": value}
        self.assertEqual(runtime.hyperv_status(target, data)["addresses"], ["10.0.0.2"])
        network[0]["switchId"] = value["switchId"]
        with self.assertRaises(RuntimeError):
            runtime.hyperv_status(target, data)

    def test_portable_slots_and_attachments_are_all_checked(self):
        for backend in ("proxmox", "virtualbox", "vmware"):
            value = spec(backend)
            if backend == "vmware":
                info = host_agent.pairs(host_agent.vmx_text(value))
                change = ("ethernet1.vnet", "vmnet3")
                extra = ("ethernet2.present", "TRUE")
            elif backend == "proxmox":
                info = {f"net{i}": f'virtio={item["mac"]},bridge={item["bridge"]}'
                        for i, item in enumerate(value["networkAdapters"])}
                change = ("net1", 'virtio=' + value["mac"] + ',bridge=vmbr1')
                extra = ("net2", info["net1"])
            else:
                info = {key: item for i, adapter in enumerate(value["networkAdapters"], 1)
                        for key, item in {f"nic{i}": "bridged", f"macaddress{i}": adapter["mac"].replace(":", ""),
                                          f"bridgeadapter{i}": adapter["bridge"]}.items()}
                change = ("bridgeadapter2", "OTHER")
                extra = ("nic3", "bridged")
            with self.subTest(backend=backend):
                self.assertTrue(host_agent.network_matches(info, value, backend))
                self.assertFalse(host_agent.network_matches({**info, change[0]: change[1]}, value, backend))
                self.assertFalse(host_agent.network_matches({**info, extra[0]: extra[1]}, value, backend))

    def test_secondary_ssh_endpoint_is_rejected_even_on_the_correct_guest(self):
        value = spec("libvirt")
        checks = guest_checks(value, "libvirt")
        shell = '''set -eu
cat() { case "$1" in */wan0/address) printf '52:54:00:12:34:01\n';; */lan0/address) printf '52:54:00:12:34:02\n';; *) return 1;; esac; }
ip() { printf '2: wan0 inet 10.0.0.2/24 scope global wan0\n'; }
''' + "; ".join(checks)
        for server, allowed in [("10.0.0.2", True), ("10.0.1.2", False)]:
            result = subprocess.run(["sh", "-c", shell], env={**os.environ, "SSH_CONNECTION": "10.0.0.1 55000 " + server + " 22"}, capture_output=True)
            self.assertEqual(result.returncode == 0, allowed)

    def test_transferred_rpc_includes_shared_adapter_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'agent.py'
            path.write_text('from vm_network import adapters\nimport json\nprint(json.dumps(adapters(' + repr(spec("vmware")) + ', "vmware")))\n')
            result = subprocess.run(["python3", "-I", "-c", python_script(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(json.loads(result.stdout)), 2)


class AdapterNixTests(unittest.TestCase):
    def test_registry_binds_target_guest_and_evaluates_installer_and_rejects_inventory_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            recipe = Path(directory) / 'recipe'
            copy_test_recipe(Path(__file__).resolve().parents[2], recipe)
            value = {"backend": "libvirt", "network": {"mode": "dhcp", "interfaces": {
                "lan0": {"mode": "static", "address": "10.123.0.1/24"}}}, "spec": spec('libvirt')}
            (recipe / 'instances/vm-demo-gateway.nix').write_text(nix(value))
            result = evaluate('''let f = builtins.getFlake ''' + nix('path:' + str(recipe)) + '''; in {
              target = f.nixberryDeployment.targets.vm-demo-gateway.spec.networkAdapters;
              guest = f.nixberrySystems.vm-demo-gateway.config.nixberry.vm.adapters;
              installer = f.nixberryInstallers.vm-demo-gateway.drvPath;
            }''')
            self.assertTrue(result['installer'].endswith('.drv'))
            self.assertEqual([item['name'] for item in result['guest']], ['wan0', 'lan0'])
            duplicate = {"backend": "libvirt", "spec": {"name": "vm-demo-other", "mac": value['spec']['networkAdapters'][1]['mac']}}
            (recipe / 'instances/vm-demo-other.nix').write_text(nix(duplicate))
            expression = '(builtins.tryEval (import ' + str(recipe / 'management/deployment') + ')).success'
            self.assertFalse(evaluate(expression))

    def test_backend_manifests_and_libvirt_xml_preserve_both_methods(self):
        with tempfile.TemporaryDirectory() as directory:
            recipe = Path(directory) / 'recipe'
            copy_test_recipe(Path(__file__).resolve().parents[2], recipe)
            cases = [{"backend": backend, "method": method, "spec": {
                **spec(backend), "storageDirectory": "/var/lib/vm-demo", "volume": "vm-demo-gateway.qcow2",
                "machine": "q35", "uri": "qemu:///system", "storagePool": "default",
                "generation": 2, "secureBoot": False, "vmid": 1000,
            }} for backend in BACKENDS for method in ("image", "iso")]
            result = evaluate('''let f = builtins.getFlake ''' + nix('path:' + str(recipe)) + ''';
              pkgs = f.inputs.nixos-raspberrypi.inputs.nixpkgs.legacyPackages.x86_64-linux;
              describe = case: let p = import (''' + str(recipe) + ''' + "/management/${case.backend}/package.nix") {
                inherit pkgs; inherit (case) spec; deploymentMethod = case.method;
                installerIso = "/nix/store/fixture-iso"; isoName = "installer.iso";
                targetSystem = "/nix/store/fixture-system"; diskoScript = "/nix/store/fixture-disko";
                guestHostName = "vm-demo-gateway"; image = "/nix/store/fixture-image";
              }; in { manifest = builtins.fromJSON p.manifest.text;
                xml = if case.backend == "libvirt" then p.domainXml.text else null;
              };
            in map describe ''' + nix(cases))
            for case, value in zip(cases, result, strict=True):
                with self.subTest(backend=case['backend'], method=case['method']):
                    self.assertEqual(value['manifest']['networkAdapters'], case['spec']['networkAdapters'])
                    self.assertEqual(value['manifest']['deploymentMethod'], case['method'])
                    if value['xml']:
                        self.assertTrue(libvirt_matches(ET.fromstring(value['xml']), case['spec']))

    def test_nix_and_python_reject_the_same_primary_identity_changes(self):
        cases = []
        for backend in BACKENDS:
            cases.append({"backend": backend, "spec": spec(backend)})
        invalid = []
        for case in cases:
            changed = copy.deepcopy(case)
            changed["spec"]["networkAdapters"].reverse()
            invalid.append(changed)
        path = Path(__file__).with_name('vm-network.nix')
        expression = '''map (case: (builtins.tryEval (builtins.deepSeq (import ''' + str(path) + ''' case) true)).success) '''
        self.assertEqual(evaluate(expression + nix(cases + invalid)), [True] * 5 + [False] * 5)

    def test_named_networks_and_luks_initrd_across_all_vm_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            recipe = Path(directory) / 'recipe'
            copy_test_recipe(Path(__file__).resolve().parents[2], recipe)
            expression = '''let f = builtins.getFlake ''' + nix('path:' + str(recipe)) + ''';
              configured = profile: f.nixosConfigurations.${profile}.extendModules { modules = [{
                nixberry.vm.adapters = [
                  { name = "wan0"; mac = "02:00:00:00:00:01"; }
                  { name = "lan0"; mac = "02:00:00:00:00:02"; }
                  { name = "spare0"; mac = "02:00:00:00:00:03"; }
                ];
                nixberry.vm.network.interfaces.lan0 = { mode = "static"; address = "10.123.0.1/24"; };
                nixberry.vm.security.mode = "luks";
              }]; };
              describe = profile: let s = (configured profile).config; in {
                networks = builtins.mapAttrs (_: n: { inherit (n) matchConfig networkConfig; }) s.systemd.network.networks;
                links = builtins.mapAttrs (_: n: { inherit (n) matchConfig linkConfig; }) s.systemd.network.links;
                initrd = { inherit (s.boot.initrd.systemd.network.networks."10-lan") matchConfig networkConfig; };
                failures = map (item: item.message) (builtins.filter (item: !item.assertion) s.assertions);
              };
            in map describe [ "nixberry-vm" "nixberry-hyperv" "nixberry-proxmox" "nixberry-virtualbox" "nixberry-vmware" ]'''
            for value in evaluate(expression):
                self.assertEqual(value['failures'], [])
                self.assertEqual(value['networks']['10-lan']['matchConfig']['MACAddress'], '02:00:00:00:00:01')
                self.assertEqual(value['networks']['20-nixberry-lan0']['networkConfig']['Address'], '10.123.0.1/24')
                self.assertNotIn('Gateway', value['networks']['20-nixberry-lan0']['networkConfig'])
                self.assertEqual(value['networks']['20-nixberry-spare0']['networkConfig']['DHCP'], 'no')
                self.assertEqual(value['links']['10-nixberry-wan0']['linkConfig']['Name'], 'wan0')
                self.assertEqual(value['initrd']['matchConfig']['MACAddress'], '02:00:00:00:00:01')


if __name__ == '__main__':
    unittest.main()
