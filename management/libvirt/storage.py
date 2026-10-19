"""Protect local disks from writes or removal when shared between VMs."""

from messages import _

import os
import xml.etree.ElementTree as ET


def same_path(first, second):
    if os.path.realpath(first) == os.path.realpath(second):
        return True
    try:
        return os.path.samefile(first, second)
    except FileNotFoundError:
        return False


def reject_shared(peer_xml, disk, nvram=None, *, virsh=None):
    root = ET.fromstring(peer_xml)
    paths = []
    for node in root.findall("./devices/disk//source"):
        paths.extend(node.get(key) for key in ("file", "dev") if node.get(key))
        if node.get("pool") or node.get("volume"):
            if not virsh or not node.get("pool") or not node.get("volume"):
                raise RuntimeError(_("Cannot verify another VM's volume path; refusing writes or removal."))
            paths.append(virsh("vol-path", node.get("volume"), "--pool", node.get("pool")))
    paths.append(root.findtext("./os/nvram"))
    paths.extend(node.get("file") for node in root.findall("./os/nvram/source"))
    protected = [path for path in (disk, nvram) if path]
    if any(path and same_path(path, target) for path in paths for target in protected):
        raise RuntimeError(_("The disk or NVRAM is shared with another VM; refusing writes or removal."))


def reject_shared_domains(virsh, name, disk, nvram=None):
    # dumpxml returns live XML for running VMs and configuration for stopped VMs.
    # A running VM's persistent configuration may reference different disks than live XML.
    persistent = set(virsh("list", "--all", "--persistent", "--name").splitlines())
    for peer in virsh("list", "--all", "--name").splitlines():
        if not peer or peer == name:
            continue
        reject_shared(virsh("dumpxml", peer), disk, nvram, virsh=virsh)
        if peer in persistent:
            reject_shared(virsh("dumpxml", peer, "--inactive"), disk, nvram, virsh=virsh)
