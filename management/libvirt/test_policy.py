"""Nový cíl musí mít vlastníka a přesné vyhrazené cesty; jméno samo nestačí."""

import unittest
import xml.etree.ElementTree as ET

import policy

SPEC = {"uri": "qemu:///system", "name": "nixberry-lab",
    "uuid": "85a5abbf-ac9f-44ad-9f34-34dd14b77123",
    "ownerId": "nixberry-85a5abbf-ac9f-44ad-9f34-34dd14b77123",
    "volume": "nixberry-lab.qcow2", "storageDirectory": "/var/lib/libvirt/images",
    "diskPath": "/var/lib/libvirt/images/nixberry-lab.qcow2", "diskGiB": 32,
    "mac": "52:54:00:11:22:33"}


class PolicyChecks(unittest.TestCase):
    def test_new_owned_target_is_allowed(self):
        policy.validate(SPEC)

    def test_unsafe_target_is_rejected(self):
        for key, value in (("uri", "qemu+ssh://remote/system"), ("name", "existing-vm"),
                           ("uuid", "invalid"), ("ownerId", "foreign"),
                           ("volume", "../other.qcow2"), ("diskPath", "/dev/sda"),
                           ("storageDirectory", "/var/lib/../other"), ("diskGiB", 0),
                           ("mac", "ff:ff:ff:ff:ff:ff")):
            with self.subTest(key=key), self.assertRaises((RuntimeError, ValueError)):
                policy.validate({**SPEC, key: value})

    def test_new_vm_cannot_be_adopted_without_owner(self):
        with self.assertRaises(RuntimeError):
            policy.validate({key: value for key, value in SPEC.items() if key != "ownerId"})

    def test_xml_owner_is_checked(self):
        for xml in ('<domain/>', '<domain><metadata><owner id="foreign"/></metadata></domain>'):
            with self.assertRaises(RuntimeError):
                policy.owner(ET.fromstring(xml), SPEC)
        policy.owner(ET.fromstring('<domain><metadata><n:owner xmlns:n="urn:nixberry:deployment" '
                                  f'id="{SPEC["ownerId"]}"/></metadata></domain>'), SPEC)


if __name__ == "__main__":
    unittest.main()
