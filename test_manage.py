import contextlib
import hashlib
import io
import json
import plistlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import manage


class ManageTests(unittest.TestCase):
    def setUp(self):
        self.example = (manage.ROOT / 'examples/providers.example.yaml').read_text()

    def test_provider_edit_preserves_key_comments_and_other_provider(self):
        other = '\n  - id: another\n    name: Other\n    base_url: https://example.com\n    api_keys: {other: PLACEHOLDER}\n    models: []\n'
        original = self.example.replace('tools:\n            supported: true', 'tools:\n            supported: false') + other
        updated = manage.prepare_provider(original, 'http://127.0.0.1:18766')
        self.assertTrue(updated.endswith(other))
        self.assertEqual(updated.split('providers:')[0], original.split('providers:')[0])
        self.assertEqual(manage.configured(updated)['api_keys'], manage.configured(original)['api_keys'])
        self.assertTrue(manage.configured(updated)['models'][0]['abilities']['tools']['supported'])
        self.assertEqual(manage.configured(updated)['base_url'], 'http://127.0.0.1:18766')

    def test_reject_different_endpoint_and_unsupported_model(self):
        for text in (self.example.replace('https://api.deepseek.com', 'https://example.com'), self.example.replace('id: deepseek-flash', 'id: another-model')):
            with self.assertRaises(SystemExit):
                manage.prepare_provider(text, 'http://127.0.0.1:18766')

    def test_dry_run_does_not_mutate_or_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            provider = Path(folder) / 'providers.yaml'
            provider.write_text(self.example)
            with patch.object(manage, 'PROVIDERS', provider), patch.object(manage, 'run') as command, patch.object(manage, 'health') as health, contextlib.redirect_stdout(io.StringIO()) as output:
                manage.install(dry_run=True)
            self.assertEqual(provider.read_text(), self.example)
            command.assert_not_called()
            health.assert_not_called()
            self.assertNotIn('YOUR_DEEPSEEK_API_KEY', output.getvalue())
            self.assertEqual(list(Path(folder).iterdir()), [provider])

    def test_rollback_preserves_later_edits(self):
        with tempfile.TemporaryDirectory() as folder:
            provider = Path(folder) / 'providers.yaml'
            record = Path(folder) / 'installation.json'
            provider.write_text(self.example + '# later edit\n')
            record.write_text(json.dumps({'installed_hash': hashlib.sha256(self.example.encode()).hexdigest(), 'backup': str(Path(folder)/'backup.yaml')}))
            with patch.object(manage, 'PROVIDERS', provider), patch.object(manage, 'INSTALL', record), patch.object(manage, 'run') as command:
                with self.assertRaises(SystemExit):
                    manage.rollback()
            command.assert_not_called()
            self.assertTrue(provider.read_text().endswith('# later edit\n'))

    def installation_environment(self, folder):
        root = Path(folder)
        (root / 'settings.json').write_text('{}')
        provider = root / 'providers.yaml'
        provider.write_text(self.example)
        data = root / 'state'
        return {'ROOT': root, 'PROVIDERS': provider, 'DATA': data, 'INSTALL': data / 'installation.json', 'PLIST': root / 'agents/adapter.plist'}

    def test_install_and_rollback_with_mocked_launchctl(self):
        with tempfile.TemporaryDirectory() as folder:
            env = self.installation_environment(folder)
            with patch.multiple(manage, **env), patch.object(manage, 'run'), patch.object(manage, 'health', side_effect=[False, True]), contextlib.redirect_stdout(io.StringIO()):
                manage.install()
                info = json.loads(env['INSTALL'].read_text())
                self.assertEqual(Path(info['backup']).read_text(), self.example)
                self.assertEqual(Path(info['backup']).stat().st_mode & 0o777, 0o600)
                self.assertEqual(manage.configured(env['PROVIDERS'].read_text())['base_url'], manage.BASE)
                plist = plistlib.loads(env['PLIST'].read_bytes())
                self.assertTrue(plist['RunAtLoad'] and plist['KeepAlive'])
                manage.rollback()
                self.assertEqual(env['PROVIDERS'].read_text(), self.example)
                self.assertFalse(env['PLIST'].exists())
                self.assertFalse(env['INSTALL'].exists())

    def test_failed_start_leaves_provider_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            env = self.installation_environment(folder)
            with patch.multiple(manage, **env), patch.object(manage, 'run'), patch.object(manage, 'health', return_value=False), patch.object(manage.time, 'sleep'):
                with self.assertRaises(SystemExit):
                    manage.install()
            self.assertEqual(env['PROVIDERS'].read_text(), self.example)
            self.assertFalse(env['PLIST'].exists())
            self.assertFalse(env['INSTALL'].exists())


if __name__ == '__main__':
    unittest.main()
