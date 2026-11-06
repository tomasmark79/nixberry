"""Minimal source account evidence for interpreting restored numeric ownership.

This catalogue is not an account database to install on the replacement system.
Unmapped numeric owners (for example container IDs) remain a restore-time decision.
"""
import grp
import json
import platform
import pwd
import unicodedata

from messages import _


def validate(value):
    def invalid():
        raise ValueError(_("Invalid source architecture or account ownership evidence."))

    if (not isinstance(value, dict) or set(value) != {'architecture', 'users', 'groups'}
            or not isinstance(value['architecture'], str)
            or value['architecture'] not in ('x86_64-linux', 'aarch64-linux')):
        invalid()
    for kind, fields in (('users', {'name', 'uid', 'gid'}), ('groups', {'name', 'gid'})):
        rows = value[kind]
        if not isinstance(rows, list) or len(rows) > 20000:
            invalid()
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != fields:
                invalid()
            name = row['name']
            if (not isinstance(name, str) or not 1 <= len(name) <= 1024
                    or any(unicodedata.category(char).startswith('C') for char in name)):
                invalid()
            for field in fields - {'name'}:
                if type(row[field]) is not int or not 0 <= row[field] < 2 ** 32 - 1:
                    invalid()
            identity = tuple(row[field] for field in sorted(fields))
            if identity in seen:
                invalid()
            seen.add(identity)
    if len(json.dumps(value, ensure_ascii=False).encode()) > 4 * 1024 * 1024:
        invalid()
    return value


def collect():
    # NSS lookups happen before the capture lease and any writer shutdown.
    users = {(entry.pw_name, entry.pw_uid, entry.pw_gid) for entry in pwd.getpwall()}
    groups = {(entry.gr_name, entry.gr_gid) for entry in grp.getgrall()}
    return validate({'architecture': platform.machine() + '-linux',
                     'users': [{'name': name, 'uid': uid, 'gid': gid} for name, uid, gid in sorted(users)],
                     'groups': [{'name': name, 'gid': gid} for name, gid in sorted(groups)]})
