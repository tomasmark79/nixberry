"""Souběžný přehled musí ukázat čerstvé výsledky i při pomalém či chybném hostiteli."""

import json
import subprocess
import threading
import unittest
from unittest.mock import Mock, patch

from discovery import collect_status


def target(backend="rpi", host="host", **extra):
    return {"backend": backend, "host": host, "spec": {"name": host},
            "capabilities": {"build": True, "export": True, "system": True, "hypervisor": backend != "rpi"}, **extra}


class DiscoveryTests(unittest.TestCase):
    def test_fast_host_is_reported_before_slow_host_finishes(self):
        delivered = threading.Event()
        registry = {"slow": target(host="slow"), "fast": target(host="fast")}
        def inspect(item):
            if item["host"] == "slow" and not delivered.wait(2):
                raise AssertionError("Rychlý hostitel musí být vypsán během čekání na pomalého")
            return {"state": "running", "addresses": []}
        order = []
        def result(name, snapshot):
            order.append(name)
            if name == "fast":
                delivered.set()
        snapshots = collect_status(registry, ["slow", "fast"], inspect, result)
        self.assertEqual(order, ["fast", "slow"])
        self.assertEqual(list(snapshots), ["slow", "fast"])

    def test_one_request_per_portable_host_and_every_invocation_is_fresh(self):
        registry = {name: target("proxmox", "pve", spec={"name": name, "sshHost": "192.168.1.1"}) for name in ("a", "b")}
        inspect = Mock(side_effect=AssertionError("Sdílený hostitel se nesmí dotazovat po jedné VM"))
        snapshots = [{"state": "running", "addresses": ["192.168.1.2", "192.168.1.1"]}, {"error": "foreign owner"}]
        with patch("discovery.rpc", return_value=snapshots) as rpc:
            for _ in range(2):
                result = collect_status(registry, ["a", "b"], inspect)
                self.assertEqual(result["a"]["addresses"], ["192.168.1.2"])
                self.assertEqual(result["b"]["state"], "error")
                self.assertIn("foreign owner", result["b"]["error"])
            self.assertEqual(rpc.call_count, 2)

    def test_unavailable_host_does_not_hide_other_results(self):
        registry = {"vm": target("proxmox", "pve"), "pi": target(host="pi")}
        with patch("discovery.rpc", side_effect=subprocess.TimeoutExpired("ssh", 25)):
            result = collect_status(registry, ["vm", "pi"], lambda _: {"state": "running", "addresses": []})
        self.assertEqual(result["vm"]["state"], "error")
        self.assertEqual(result["pi"]["state"], "running")

    def test_hyperv_uses_one_light_query_and_checks_each_owner(self):
        spec = {"name": "a", "windowsComputerName": "WINDOWS-DEMO", "ownerId": "owner", "generation": 2,
                "mac": "027981000001", "switchName": "LAN", "sshHost": "192.168.1.1"}
        registry = {name: target("hyperv", "windows", commands={"probe": "/probe"}, spec={**spec, "name": name}) for name in ("a", "b")}
        vm = {"name": "a", "ownerId": "owner", "generation": 2, "state": "Running",
              "network": [{"mac": spec["mac"], "switchName": "LAN", "ips": ["192.168.1.2"]}]}
        data = {"computerName": "WINDOWS-DEMO", "hypervModule": True, "vms": [vm, {**vm, "name": "b", "ownerId": "foreign"}]}
        with patch("discovery.read", return_value=json.dumps(data)) as read:
            result = collect_status(registry, ["a", "b"], Mock())
        read.assert_called_once()
        self.assertIn("--status-only", read.call_args.args[0])
        self.assertEqual(result["a"]["state"], "running")
        self.assertEqual(result["b"]["state"], "error")

    def test_empty_inventory_never_connects(self):
        inspect = Mock()
        self.assertEqual(collect_status({}, [], inspect), {})
        inspect.assert_not_called()

    def test_libvirt_lists_host_once_and_rejects_remote_uri(self):
        registry = {name: target("libvirt", "local", spec={"name": name, "uri": "qemu:///system"}) for name in ("a", "b")}
        with patch("discovery.read", return_value="a\nb") as read, \
                patch("discovery.libvirt_status", return_value={"state": "running", "addresses": []}) as inspect:
            collect_status(registry, ["a", "b"], Mock())
        read.assert_called_once()
        self.assertEqual(inspect.call_count, 2)
        self.assertEqual(inspect.call_args.args[1], ["a", "b"])
        registry["a"]["spec"]["uri"] = "qemu+ssh://foreign/system"
        with patch("discovery.read") as read:
            result = collect_status(registry, ["a"], Mock(side_effect=RuntimeError("unsupported URI")))
        read.assert_not_called()
        self.assertEqual(result["a"]["state"], "error")
