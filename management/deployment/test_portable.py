"""Kontrakty adaptérů, ochrany vlastníka a skutečný přenos protokolu stdin."""

import hashlib
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

import host_agent as agent
import portable
import runtime
from scaffold import new_vm


def spec(root, **extra):
    return {"hostPlatform": "linux", "hostName": socket.gethostname(), "transport": "local",
            "storageDirectory": str(root), "name": "nixberry-test", "uuid": "12345678-1234-4321-8123-123456789abc",
            "ownerId": "nixberry-12345678-1234-4321-8123-123456789abc", "mac": "00:50:56:12:34:56",
            "memoryMiB": 4096, "vcpus": 2, "diskGiB": 32, "toolPath": "vmrun", **extra}


class PortableTests(unittest.TestCase):
    def test_verification_failure_does_not_request_another_disk_passphrase(self):
        for backend in ("proxmox", "virtualbox", "vmware"):
            target = {"backend": backend, "spec": spec(self.root), "security": {"mode": "luks"}}
            manifest = {"security": target["security"], "systemPath": "/nix/store/expected-system"}
            failure = RuntimeError("Installed-system identity mismatch")
            failure.returncode = 1
            with self.subTest(backend=backend), \
                    patch.object(portable, "snapshot", return_value={"state": "running", "addresses": ["192.168.50.20"]}), \
                    patch.object(portable, "known_hosts", return_value=self.root / "ssh/known_hosts"), \
                    patch.object(runtime, "verify", side_effect=failure), \
                    patch.object(portable.time, "monotonic", side_effect=[0, 0, 2]), \
                    patch.object(portable.time, "sleep"), patch("security.unlock_for_install") as unlock:
                with self.assertRaisesRegex(RuntimeError, "Installed-system identity mismatch"):
                    portable.start_and_verify(target, manifest, timeout=1)
                unlock.assert_not_called()

    def test_remote_errors_preserve_transport_exit_status(self):
        for code in (1, 255):
            with patch.object(runtime.subprocess, "run", return_value=subprocess.CompletedProcess([], code, "detail", "failure")):
                with self.assertRaises(RuntimeError) as failure:
                    runtime.read(["ssh", "host", "command"])
            self.assertEqual(failure.exception.returncode, code)
            self.assertEqual(str(failure.exception), "failure\ndetail")

    def test_windows_private_directory_grants_actual_user_sid(self):
        directory = Mock()
        with patch.object(agent.os, "name", "nt"), \
                patch.object(agent, "run", side_effect=['"WINDOWS-DEMO\\user","S-1-5-21-123-456-789-1001"', "OK"]) as run:
            agent.private_directory(directory)
        directory.mkdir.assert_called_once_with(mode=0o700)
        self.assertEqual(run.call_args.args[0], ["icacls.exe", str(directory), "/inheritance:r", "/grant:r",
            "*S-1-5-21-123-456-789-1001:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"])
        with patch.object(agent.os, "name", "nt"), patch.object(agent, "run", return_value="bad") as run:
            with self.assertRaisesRegex(RuntimeError, "SID"):
                agent.private_directory(Mock())
            run.assert_called_once()

    def test_shutdown_retries_only_virtualbox_transition_error(self):
        transient = RuntimeError("VBoxManage: VBOX_E_INVALID_OBJECT_STATE")
        with patch.object(portable, "rpc", side_effect=[transient, {"state": "shut off"}]) as rpc, \
                patch.object(portable.time, "sleep"):
            portable.wait_stopped({"backend": "virtualbox"})
            self.assertEqual(rpc.call_count, 2)
            self.assertTrue(all(call.args[1] == "probe" for call in rpc.call_args_list))
        for backend, failure in (("vmware", transient), ("virtualbox", RuntimeError("owner mismatch"))):
            with patch.object(portable, "rpc", side_effect=failure) as rpc, self.assertRaises(RuntimeError):
                portable.wait_stopped({"backend": backend})
            rpc.assert_called_once()

    def test_shutdown_transient_error_has_finite_deadline(self):
        with patch.object(portable, "rpc", side_effect=RuntimeError("VBOX_E_INVALID_OBJECT_STATE")) as rpc, \
                patch.object(portable.time, "monotonic", side_effect=[0, 1, 121]), \
                patch.object(portable.time, "sleep"), self.assertRaisesRegex(RuntimeError, "ISO will not be detached"):
            portable.wait_stopped({"backend": "virtualbox"})
        self.assertTrue(all(call.args[1] == "probe" for call in rpc.call_args_list))

    def test_inventory_rpc_dispatch_calls_inventory_for_each_backend(self):
        for backend in ("virtualbox", "vmware", "proxmox"):
            with self.subTest(backend=backend), patch.object(agent, "host_check", return_value=Path("/owned")), \
                    patch.object(agent, "inventory", return_value={"images": []}) as inventory:
                request = {"spec": {"name": "test"}, "backend": backend,
                           "action": "inventory", "declared": [{"name": "test"}]}
                self.assertEqual(agent.dispatch(request), {"images": []})
                inventory.assert_called_once_with(request["spec"], backend, Path("/owned"), request["declared"])

    def test_windows_vm_launcher_escapes_ssh_job_and_reports_errors(self):
        with patch.object(agent.os, "name", "nt"), patch.object(agent.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, b"started", b"")
            self.assertEqual(agent.run(["vmrun", "start"], breakaway=True), "started")
            self.assertEqual(run.call_args.kwargs["creationflags"], 0x01000000)
            agent.run(["vmrun", "list"])
            self.assertNotIn("creationflags", run.call_args.kwargs)
            run.return_value = subprocess.CompletedProcess([], 1, b"", b"Access denied")
            with self.assertRaisesRegex(RuntimeError, "Access denied"):
                agent.run(["vmrun", "start"], breakaway=True)

    def test_virtualbox_72_and_legacy_guest_properties_have_the_same_values(self):
        values = {"/VirtualBox/GuestInfo/Net/0/MAC": "0279813B6995",
                  "/VirtualBox/GuestInfo/Net/0/V4/IP": "192.168.79.230",
                  "/VirtualBox/GuestInfo/Net/1/MAC": "001122334455",
                  "/VirtualBox/GuestInfo/Net/1/V4/IP": "192.168.79.1"}
        legacy = "\n".join(f"Name: {key}, value: {value}, timestamp: 123, flags: TRANSIENT" for key, value in values.items())
        table = "\n".join(f"{key:45} = '{value}' @ 2026-10-18T09:03:18.035Z TRANSIENT" for key, value in values.items())
        for output in (legacy, table):
            with self.subTest(output=output):
                self.assertEqual(agent.vbox_guest_properties(output), values)

    def test_offline_rpc_never_connects(self):
        with patch("subprocess.run") as run, self.assertRaisesRegex(RuntimeError, "The offline target"):
            portable.rpc({"backend": "proxmox", "spec": {"transport": "offline"}}, "probe")
        run.assert_not_called()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def marker(self, s, data=b"disk", backend="vmware"):
        directory = self.root / s["name"]
        directory.mkdir()
        (directory / "nixberry.json").write_text(json.dumps({"spec": s, "sha256": hashlib.sha256(data).hexdigest(), "imageBytes": len(data)}))
        (directory / agent.image_name(backend)).write_bytes(data)
        return directory

    def test_invalid_specs_fail_before_subprocess(self):
        for change in ({"ownerId": "foreign"}, {"uuid": "bad"}, {"mac": "01:00:00:00:00:01"},
                       {"name": "../foreign"}, {"memoryMiB": True}, {"diskGiB": 0}, {"mac": "02:79:81:00:00:01"}):
            with self.subTest(change=change), patch.object(agent, "run") as run:
                with self.assertRaises(ValueError):
                    agent.host_check(spec(self.root, **change), "vmware")
                run.assert_not_called()

    def test_wrong_host_and_symlink_storage_rejected_before_cli(self):
        with patch.object(agent, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "hostname"):
                agent.host_check(spec(self.root, hostName="foreign"), "vmware")
            link = self.root / "link"
            link.symlink_to(self.root, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, "link"):
                agent.host_check(spec(link), "vmware")
            run.assert_not_called()

    def test_wrong_owner_or_checksum_cannot_create(self):
        s = spec(self.root)
        directory = self.marker(s)
        (directory / "system.vmdk").write_bytes(b"corrupted")
        with patch.object(agent, "run") as run, self.assertRaisesRegex(RuntimeError, "checksum"):
            agent.create(s, "vmware", directory)
        run.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "declaration"):
            agent.owned(directory, {**s, "memoryMiB": 8192})

    def test_vmx_round_trip_and_foreign_config_rejected(self):
        s = spec(self.root)
        self.assertEqual(agent.bios_uuid(s), "78 56 34 12 34 12 21 43-81 23 12 34 56 78 9a bc")
        directory = self.marker(s)
        vmx = directory / (s["name"] + ".vmx")
        vmx.write_text(agent.vmx_text(s))
        with patch.object(agent, "run", return_value="Total running VMs: 0"):
            self.assertEqual(agent.probe(s, "vmware", directory)["state"], "shut off")
            for key in ("annotation", "sata0:0.fileName", "ethernet0.address", "uuid.bios", "firmware"):
                vmx.write_text(agent.vmx_text(s).replace(key + ' = "', key + ' = "foreign'))
                with self.subTest(key=key), self.assertRaises(RuntimeError):
                    agent.probe(s, "vmware", directory)

    def test_vmware_headless_ip_wait_is_bounded_while_tools_start(self):
        s = spec(self.root)
        directory = self.marker(s)
        vmx = directory / (s["name"] + ".vmx")
        vmx.write_text(agent.vmx_text(s))
        def run(command, **kwargs):
            if command[1] == "list":
                return "Total running VMs: 1\n" + str(vmx)
            self.assertEqual(command[-1], "-wait")
            self.assertEqual(kwargs["timeout"], 5)
            raise subprocess.TimeoutExpired(command, 5)
        with patch.object(agent, "run", side_effect=run):
            self.assertEqual(agent.probe(s, "vmware", directory), {"state": "running", "addresses": []})

    def test_actual_rpc_stream_upload_exclusive_create_and_probe(self):
        # Skutečný Python proces, skutečný přenos stdin a soubory; hypervizor
        # nahrazuje krátký externí proces, který hlásí prázdný Workstation.
        tool = self.root / "vmrun-fixture"
        tool.write_text('#!' + sys.executable + '\nimport sys\nassert sys.argv[1:] == ["list"]\nprint("Total running VMs: 0")\n')
        tool.chmod(0o700)
        # Přenosový fixture zapisuje jen malý soubor; nepotřebuje rezervu
        # 32 GiB jako skutečná výchozí VM. Kontroly kapacity mají vlastní testy.
        s = spec(self.root, toolPath=str(tool), diskGiB=8)
        target = {"backend": "vmware", "spec": s}
        image = self.root / "source.vmdk"
        image.write_bytes(b"test-disk\x00\xff" * 10000)
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        self.assertEqual(portable.rpc(target, "plan")["state"], "absent")
        self.assertFalse((self.root / s["name"]).exists())
        portable.rpc(target, "prepare", imageBytes=image.stat().st_size, sha256=digest)
        result = portable.rpc(target, "upload", image=image)
        self.assertEqual(result["sha256"], digest)
        self.assertEqual(portable.rpc(target, "create")["state"], "shut off")
        self.assertEqual(portable.rpc(target, "probe")["state"], "shut off")
        for action in ("prepare", "upload", "create"):
            with self.subTest(action=action), self.assertRaises(RuntimeError):
                portable.rpc(target, action, image=image if action == "upload" else None,
                             **({"imageBytes": image.stat().st_size, "sha256": digest} if action == "prepare" else {}))

    def test_failed_transfer_cannot_be_reused(self):
        s = spec(self.root)
        directory = self.marker(s, data=b"expected")
        (directory / "system.vmdk").unlink()
        stream = type("Input", (), {"buffer": io.BytesIO(b"wrong")})()
        with patch.object(agent, "host_check", return_value=directory), patch.object(agent.sys, "stdin", stream):
            with self.assertRaisesRegex(RuntimeError, "corrupted"):
                agent.dispatch({"spec": s, "backend": "vmware", "action": "upload"})
        with patch.object(agent, "run") as run, self.assertRaisesRegex(RuntimeError, "checksum"):
            agent.create(s, "vmware", directory)
        run.assert_not_called()

    def test_windows_ssh_command_is_short_and_streams_source(self):
        s = spec(self.root, hostPlatform="windows", transport="ssh", sshHost="192.168.50.123", sshUser="example")
        target = {"backend": "vmware", "spec": s}
        response = subprocess.CompletedProcess([], 0, b'{"ok":true,"result":{"state":"absent","addresses":[]}}', b'')
        with patch.object(portable.subprocess, "run", return_value=response) as run:
            portable.rpc(target, "probe")
        command = run.call_args.args[0]
        self.assertLess(len(command[-1]), 200)
        self.assertTrue(command[-1].startswith('python -c "import sys;exec('))
        self.assertIn(b"def host_check", run.call_args.kwargs["input"])
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertIn("StrictHostKeyChecking=yes", command)

    def test_protected_host_never_connects(self):
        s = spec(self.root, transport="ssh", sshHost="192.168.79.1", sshUser="root")
        with patch.object(portable.subprocess, "run") as run, self.assertRaises(ValueError):
            portable.rpc({"backend": "vmware", "spec": s}, "probe")
        run.assert_not_called()

    def test_virtualbox_agent_address_belongs_to_expected_mac(self):
        s = spec(self.root, bridge="Ethernet Adapter")
        directory = self.marker(s, backend="virtualbox")
        info = f'VMState="running"\nfirmware="EFI"\nmacaddress1="005056123456"\n"SATA-0-0"="{directory / "system.vdi"}"\nnic1="bridged"\nbridgeadapter1="Ethernet Adapter"'
        properties = ('Name: /VirtualBox/GuestInfo/Net/0/MAC, value: 005056123456, timestamp: 1\n'
                      'Name: /VirtualBox/GuestInfo/Net/0/V4/IP, value: 192.168.79.130, timestamp: 1\n'
                      'Name: /VirtualBox/GuestInfo/Net/1/MAC, value: AABBCCDDEEFF, timestamp: 1\n'
                      'Name: /VirtualBox/GuestInfo/Net/1/V4/IP, value: 192.168.79.1, timestamp: 1')
        with patch.object(agent, "run", side_effect=[f'"{s["name"]}" {{{s["uuid"]}}}', info,
                                                    "Value: " + s["ownerId"], properties]):
            self.assertEqual(agent.probe(s, "virtualbox", directory)["addresses"], ["192.168.79.130"])

    def test_proxmox_collision_in_cluster_rejected(self):
        s = spec(self.root, vmid=1000, node="pve", storage="local-lvm", bridge="vmbr0")
        with patch.object(agent, "proxmox_registry", return_value=[{"vmid": 1000, "name": "foreign", "node": "other"}]), \
                patch.object(agent, "run") as run, self.assertRaisesRegex(RuntimeError, "already in use"):
            agent.absent(s, "proxmox", self.root / s["name"])
        run.assert_not_called()

    def test_proxmox_probe_uses_node_when_cluster_cache_lags(self):
        s = spec(self.root, vmid=1000, node="pve", storage="local-lvm", bridge="vmbr0")
        directory = self.marker(s, backend="proxmox")
        volume = "local-lvm:vm-1000-disk-1"
        (directory / "hardware.json").write_text(json.dumps({"diskVolume": volume}))
        config = {"description": s["ownerId"], "name": s["name"], "smbios1": "uuid=" + s["uuid"],
                  "bios": "ovmf", "net0": "virtio=" + s["mac"] + ",bridge=vmbr0", "virtio0": volume}
        for cluster, current in (([], "stopped"),
                                 ([{"vmid": 1000, "name": s["name"], "node": "pve", "type": "qemu", "status": "stopped"}], "running")):
            def respond(command, **kwargs):
                if command[:3] == ["qm", "guest", "cmd"]:
                    return "[]"
                if command[2].endswith("/config"):
                    return json.dumps(config)
                if command[2].endswith("/status/current"):
                    return json.dumps({"status": current})
                self.assertEqual(command[2], "/nodes/pve/qemu")
                return json.dumps([{"vmid": 1000, "name": s["name"], "status": "stopped"}])
            with self.subTest(current=current), patch.object(agent, "proxmox_registry", return_value=cluster), \
                    patch.object(agent, "run", side_effect=respond):
                self.assertEqual(agent.probe(s, "proxmox", directory)["state"],
                                 "running" if current == "running" else "shut off")
        config["description"] = "foreign-owner"
        with patch.object(agent, "proxmox_registry", return_value=[]), \
                patch.object(agent, "run", side_effect=respond), self.assertRaisesRegex(RuntimeError, "owner"):
            agent.probe(s, "proxmox", directory)

    def test_proxmox_creation_uses_uefi_virtio_import_and_owner(self):
        s = spec(self.root, vmid=1000, node="pve", storage="local-lvm", bridge="vmbr0")
        directory = self.marker(s, backend="proxmox")
        with patch.object(agent, "proxmox_registry", return_value=[]), patch.object(agent, "run", side_effect=["", '{"virtio0":"local-lvm:vm-1000-disk-1,size=32G","efidisk0":"local-lvm:vm-1000-disk-0"}']) as run, \
                patch.object(agent, "probe", return_value={"state": "shut off", "addresses": []}):
            agent.create(s, "proxmox", directory)
        command = run.call_args_list[0].args[0]
        self.assertEqual(command[:3], ["qm", "create", "1000"])
        self.assertEqual(command[command.index("--bios") + 1], "ovmf")
        self.assertIn(s["ownerId"], command)
        self.assertIn("local-lvm:0,import-from=" + str(directory / "system.qcow2"), command)
        self.assertIn("virtio=" + s["mac"] + ",bridge=vmbr0", command)

    def test_proxmox_iso_uses_managed_volume_and_detaches_only_verified_medium(self):
        s = spec(self.root, vmid=1000, node="pve", storage="local-lvm", bridge="vmbr0")
        directory = self.marker(s, backend="proxmox")
        marker = json.loads((directory / "nixberry.json").read_text())
        marker["method"] = "iso"
        (directory / "nixberry.json").write_text(json.dumps(marker))
        (directory / "system.qcow2").rename(directory / "installer.iso")
        medium = self.root / "managed.iso"
        volume = "local:iso/nixberry-" + s["uuid"] + ".iso"
        config = dict(virtio0="local-lvm:vm-1000-disk-0,size=32G",
                      efidisk0="local-lvm:vm-1000-disk-1,size=4M", ide2=volume + ",media=cdrom")
        with patch.object(agent, "proxmox_registry", return_value=[]), \
                patch.object(agent, "proxmox_iso_medium", return_value=(volume, medium)), \
                patch.object(agent, "probe", return_value={"state": "shut off", "addresses": []}), \
                patch.object(agent, "run", side_effect=["", json.dumps(config)]) as run:
            agent.create(s, "proxmox", directory)
            self.assertIn(volume + ",media=cdrom", run.call_args_list[0].args[0])
        self.assertEqual(medium.read_bytes(), b"disk")
        with patch.object(agent, "proxmox_iso_medium", return_value=(volume, medium)), \
                patch.object(agent, "probe", return_value={"state": "shut off", "addresses": []}), \
                patch.object(agent, "run", return_value=json.dumps({**config, "ide2": "local:iso/foreign.iso"})) as run:
            with self.assertRaisesRegex(RuntimeError, "expected ISO"):
                agent.installer_media(s, "proxmox", directory, detach=True)
            self.assertEqual(run.call_count, 1)
            self.assertTrue(medium.exists())
        with patch.object(agent, "proxmox_iso_medium", return_value=(volume, medium)), \
                patch.object(agent, "probe", return_value={"state": "shut off", "addresses": []}), \
                patch.object(agent, "run", side_effect=[json.dumps(config), ""]) as run:
            agent.installer_media(s, "proxmox", directory, detach=True)
            self.assertIn("--delete", run.call_args.args[0])
        self.assertFalse(medium.exists())
        self.assertEqual((directory / "installer.iso").read_bytes(), b"disk")

    def test_proxmox_iso_refuses_existing_file_and_storage_without_iso(self):
        s = spec(self.root, vmid=1000, node="pve", storage="local-lvm", bridge="vmbr0")
        with patch.object(agent, "run", return_value='{"content":"images"}') as run:
            with self.assertRaisesRegex(RuntimeError, "enabled for ISO"):
                agent.proxmox_iso_medium(s)
            self.assertEqual(run.call_count, 1)
        with patch.object(agent, "run", side_effect=['{"content":"iso"}', '{"active":0,"enabled":1}']) as run:
            with self.assertRaisesRegex(RuntimeError, "inactive"):
                agent.proxmox_iso_medium(s)
            self.assertEqual(run.call_count, 2)
        directory = self.marker(s, backend="proxmox")
        marker = json.loads((directory / "nixberry.json").read_text())
        marker["method"] = "iso"
        (directory / "nixberry.json").write_text(json.dumps(marker))
        (directory / "system.qcow2").rename(directory / "installer.iso")
        medium = self.root / "foreign.iso"
        medium.write_bytes(b"keep")
        with patch.object(agent, "proxmox_registry", return_value=[]), \
                patch.object(agent, "proxmox_iso_medium", return_value=("local:iso/owned.iso", medium)), \
                patch.object(agent, "run") as run:
            with self.assertRaises(FileExistsError):
                agent.create(s, "proxmox", directory)
            run.assert_not_called()
        self.assertEqual(medium.read_bytes(), b"keep")

    def test_proxmox_batch_shares_live_inventory_but_checks_each_owner(self):
        node = socket.gethostname().split(".")[0]
        first = spec(self.root, vmid=1000, node=node, storage="local-lvm", bridge="vmbr0")
        identity = "22345678-1234-4321-8123-123456789abc"
        second = {**first, "name": "nixberry-second", "vmid": 1001, "uuid": identity, "ownerId": "nixberry-" + identity}
        configs = {}
        machines = []
        for item in (first, second):
            directory = self.marker(item, backend="proxmox")
            volume = "local-lvm:vm-" + str(item["vmid"]) + "-disk-1"
            (directory / "hardware.json").write_text(json.dumps({"diskVolume": volume}))
            configs[item["vmid"]] = {"description": item["ownerId"], "name": item["name"], "smbios1": "uuid=" + item["uuid"],
                                      "bios": "ovmf", "net0": "virtio=" + item["mac"] + ",bridge=vmbr0", "virtio0": volume}
            machines.append({"vmid": item["vmid"], "name": item["name"], "node": node, "type": "qemu", "status": "stopped"})
        def respond(command, **kwargs):
            if command == ["pveversion"]:
                return "pve-manager"
            if command[2].endswith("/config"):
                return json.dumps(configs[int(command[2].split("/")[4])])
            if command[2].endswith("/status/current"):
                return '{"status":"stopped"}'
            self.assertEqual(command[2], "/nodes/" + node + "/qemu")
            return json.dumps(machines)
        request = {"spec": first, "backend": "proxmox", "action": "probe-many", "specs": [first, second]}
        with patch.object(agent, "proxmox_registry", return_value=machines) as cluster, \
                patch.object(agent, "run", side_effect=respond) as run:
            self.assertEqual([row["state"] for row in agent.dispatch(request)], ["shut off", "shut off"])
            cluster.assert_called_once()
            self.assertEqual(sum(call.args[0] == ["pveversion"] for call in run.call_args_list), 1)
            configs[1001]["description"] = "foreign"
            result = agent.dispatch(request)
            self.assertEqual(result[0]["state"], "shut off")
            self.assertIn("error", result[1])
            self.assertEqual(cluster.call_count, 2)

    def test_virtualbox_creation_has_unique_medium_uuid_before_attach(self):
        s = spec(self.root, bridge="Ethernet Adapter")
        directory = self.marker(s, backend="virtualbox")
        with patch.object(agent, "vbox_registry", return_value=[]), patch.object(agent, "run") as run, \
                patch.object(agent, "probe", return_value={"state": "shut off", "addresses": []}):
            agent.create(s, "virtualbox", directory)
        commands = [call.args[0] for call in run.call_args_list]
        attach = next(command for command in commands if command[1] == "storageattach")
        self.assertEqual(attach[attach.index("--setuuid") + 1], s["uuid"])
        modify = next(command for command in commands if command[1] == "modifyvm")
        self.assertEqual(modify[modify.index("--system-uuid-le") + 1], "off")
        self.assertIn("--bridge-adapter1", modify)
        self.assertIn("efi", modify)
        self.assertFalse(any(command[1] == "startvm" for command in commands))

    def test_insufficient_space_leaves_no_directory(self):
        s = spec(self.root)
        with patch.object(agent, "host_check", return_value=self.root / s["name"]), \
                patch.object(agent, "absent"), patch.object(agent.shutil, "disk_usage", return_value=type("Disk", (), {"free": 10})()):
            for action in ("plan", "prepare"):
                with self.subTest(action=action), self.assertRaisesRegex(RuntimeError, "space"):
                    agent.dispatch({"spec": s, "backend": "vmware", "action": action, "imageBytes": 100})
        self.assertFalse((self.root / s["name"]).exists())

    def test_runtime_new_backends_use_correct_disk_agent_and_jump_host(self):
        for backend, service, disk in (("proxmox", "qemu-guest-agent", "/dev/vda2"),
                                       ("virtualbox", "virtualbox", "/dev/sda2"), ("vmware", "vmware", "/dev/sda2")):
            s = spec(self.root, transport="ssh", sshHost="192.168.50.123", sshUser="example")
            target = {"backend": backend, "spec": s}
            snapshot = {"state": "running", "addresses": ["10.0.0.2"]}
            command = runtime.ssh_command(target, snapshot)
            self.assertIn("example@192.168.50.123", command)
            self.assertIn("StrictHostKeyChecking=yes", command)
            with patch.object(runtime, "status", return_value=snapshot), patch.object(runtime, "read", return_value="vm\n/nix/store/system\n" + disk) as read:
                runtime.verify(target, "/nix/store/system")
            script = read.call_args.args[0][-1]
            for expected in (service, disk, s["uuid"], s["mac"]):
                self.assertIn(expected, script)

    def test_new_declarations_are_local_exclusive_and_platform_specific(self):
        (self.root / "flake.nix").touch()
        (self.root / "configurations").mkdir()
        (self.root / "configurations/default.nix").write_text("{}")
        directory = self.root / "instances"
        directory.mkdir(parents=True)
        (directory / "default.nix").touch()
        for backend in ("proxmox", "virtualbox", "vmware"):
            hosts = {"host": {"backend": backend, "connection": {}}}
            path, text = new_vm(str(self.root), hosts, {}, backend, backend, write=False)
            self.assertFalse(path.exists())
            self.assertIn('profile = "nixberry-' + backend + '";', text)
            path, _ = new_vm(str(self.root), hosts, {}, backend, backend)
            self.assertTrue(path.is_file())
            with self.assertRaises(RuntimeError):
                new_vm(str(self.root), hosts, {}, backend, backend)

    def test_virtualbox_inventory_protects_snapshot_parents(self):
        first, second = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
        (self.root / "base.vdi").write_bytes(b"base")
        (self.root / "snapshot.vdi").write_bytes(b"snapshot")
        cfg = self.root / "test.vbox"
        cfg.write_text(f'<VirtualBox xmlns="urn:virtualbox"><Machine><Snapshot><Image uuid="{{{second}}}"/></Snapshot></Machine></VirtualBox>')
        listing = (f'UUID: {first}\nParent UUID: base\nLocation: {self.root / "base.vdi"}\nCapacity: 32768 MBytes\n\n'
                   f'UUID: {second}\nParent UUID: {first}\nLocation: {self.root / "snapshot.vdi"}\nCapacity: 32768 MBytes')
        s = spec(self.root, bridge="bridge")
        with patch.object(agent, "run", side_effect=[f'"old-vm" {{{s["uuid"]}}}', f'VMState="poweroff"\nCfgFile="{cfg}"', listing]):
            result = agent.virtualbox_inventory(s, [])
        self.assertEqual(result["errors"], [])
        self.assertEqual([i["classification"] for i in result["images"]], ["protected", "protected"])
        self.assertEqual(result["machines"][0]["classification"], "review")
        self.assertGreater(result["machines"][0]["allocatedBytes"], 0)

    def test_vmware_inventory_includes_stopped_vm_and_recovery_files(self):
        s = spec(self.root)
        directory = self.marker(s)
        (directory / (s["name"] + ".vmx")).write_text(agent.vmx_text(s))
        (directory / "old-snapshot.vmdk").write_bytes(b"snapshot")
        with patch.object(agent, "run", return_value="Total running VMs: 0"):
            result = agent.inventory(s, "vmware", directory, [s])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["machines"][0]["state"], "shut off")
        self.assertTrue(all(image["classification"] == "protected" for image in result["images"]))

    def test_proxmox_snapshot_and_block_allocation_remain_explicit(self):
        s = spec(self.root, vmid=1000, node="pve", storage="local-lvm", bridge="vmbr0")
        machines = [{"vmid": 1000, "name": "test", "status": "stopped", "node": "pve", "type": "qemu", "maxdisk": 1024}]
        def respond(command):
            if command[0] == "pvesm": return "/dev/not-a-real-volume"
            path = command[2]
            if path.endswith("/snapshot"): return '[{"name":"current"},{"name":"before"}]'
            if path.endswith("/snapshot/before/config"): return '{"virtio0":"local-lvm:vm-1000-old,size=32G"}'
            if path.endswith("/config"): return '{"virtio0":"local-lvm:vm-1000-current,size=32G"}'
            if path.endswith("/content"): return '[{"volid":"local-lvm:vm-1000-current","size":1024},{"volid":"local-lvm:vm-1000-old","size":1024}]'
            raise AssertionError(command)
        with patch.object(agent, "proxmox_registry", return_value=machines), patch.object(agent, "run", side_effect=respond):
            result = agent.proxmox_inventory(s, [s])
        self.assertEqual(result["errors"], [])
        self.assertTrue(all(image["classification"] == "protected" for image in result["images"]))
        self.assertIsNone(result["machines"][0]["allocatedBytes"])

    def test_nix_packages_include_runnable_adapters(self):
        from check_inventory import nix
        recipe = Path(__file__).resolve().parents[2]
        for backend, method in ((b, m) for b in ("proxmox", "virtualbox", "vmware") for m in ("image", "iso")):
            s = spec(self.root, hostName="fixture.invalid", bridge="vmbr0", node="pve", storage="local-lvm", vmid=1000)
            expression = (
                "let f = builtins.getFlake " + nix("path:" + str(recipe)) + "; "
                "pkgs = f.inputs.nixos-raspberrypi.inputs.nixpkgs.legacyPackages.x86_64-linux; "
                'fixture = pkgs.writeText "nixberry-test-fixture" "fixture"; in '
                "import " + str(recipe / "management" / backend / "package.nix") + " { inherit pkgs; "
                + "spec = " + nix(s) + '; guestHostName = "fixture"; image = fixture; '
                + 'targetSystem = fixture; diskoScript = fixture; installerIso = fixture; isoName = "fixture.iso"; '
                + 'deploymentMethod = "' + method + '"; }')
            with self.subTest(backend=backend, method=method):
                result = subprocess.run(["nix", "build", "--impure", "--no-link", "--print-out-paths", "--expr", expression],
                                        text=True, capture_output=True, timeout=120)
                self.assertEqual(result.returncode, 0, result.stderr[-3000:])
                package = Path(result.stdout.strip())
                for action in ("probe", "create", "install", "provision"):
                    command = package / "bin" / ("nixberry-" + backend + "-" + action)
                    help_result = subprocess.run([str(command), "--help"], text=True, capture_output=True, timeout=15)
                    self.assertEqual(help_result.returncode, 0, help_result.stderr)
                # Balíček musí skutečně načíst hostitelský adaptér a odmítnout
                # jiný hostname dříve, než by volal hypervizor nebo měnil soubory.
                result = subprocess.run([str(package / "bin" / ("nixberry-" + backend + "-probe"))],
                                        text=True, capture_output=True, timeout=15)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("The host hostname does not match", result.stderr)


if __name__ == "__main__":
    unittest.main()
