"""Source account evidence is bounded and never copies login credentials."""
import copy
import grp
import pwd
import unittest
from unittest.mock import patch

import backup_identity


class IdentityTests(unittest.TestCase):
    def test_collects_numeric_names_without_passwords_or_login_settings(self):
        users = [pwd.struct_passwd(('player', 'secret', 1000, 100, 'Private name', '/home/private', '/bin/sh'))]
        groups = [grp.struct_group(('games', 'secret', 100, ['private-member']))]
        with patch('backup_identity.platform.machine', return_value='aarch64'), \
                patch('backup_identity.pwd.getpwall', return_value=users), \
                patch('backup_identity.grp.getgrall', return_value=groups):
            self.assertEqual(backup_identity.collect(), {
                'architecture': 'aarch64-linux',
                'users': [{'name': 'player', 'uid': 1000, 'gid': 100}],
                'groups': [{'name': 'games', 'gid': 100}]})

    def test_accepts_numeric_aliases_and_unicode_names_without_inventing_accounts(self):
        evidence = {'architecture': 'x86_64-linux',
                    'users': [{'name': 'hráč', 'uid': 1000, 'gid': 999},
                              {'name': 'alias', 'uid': 1000, 'gid': 999}], 'groups': []}
        self.assertEqual(backup_identity.validate(evidence), evidence)

    def test_rejects_duplicate_or_malformed_evidence(self):
        original = {'architecture': 'x86_64-linux', 'users': [{'name': 'root', 'uid': 0, 'gid': 0}],
                    'groups': [{'name': 'root', 'gid': 0}]}
        malformed = []
        for architecture in ('armv7l-linux', [], None):
            malformed.append({**original, 'architecture': architecture})
        for key, value in (('uid', True), ('uid', -1), ('gid', 2 ** 32 - 1),
                           ('name', ''), ('name', 'bad\nname'), ('name', 'x' * 1025)):
            item = copy.deepcopy(original)
            item['users'][0][key] = value
            malformed.append(item)
        malformed.extend([{**original, 'users': original['users'] * 2},
                          {**original, 'password': 'secret'},
                          {**original, 'groups': [{'name': 'root', 'gid': 0, 'members': []}]}])
        for evidence in malformed:
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                backup_identity.validate(evidence)

    def test_rejects_unbounded_account_metadata(self):
        many = [{'name': str(index), 'gid': index} for index in range(20001)]
        with self.assertRaises(ValueError):
            backup_identity.validate({'architecture': 'x86_64-linux', 'users': [], 'groups': many})
        large = [{'name': str(index) + 'x' * 1000, 'gid': index} for index in range(5000)]
        with self.assertRaises(ValueError):
            backup_identity.validate({'architecture': 'x86_64-linux', 'users': [], 'groups': large})
