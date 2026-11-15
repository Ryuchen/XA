"""Read-only tests of the actual Docker Compose model; never starts a service."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent


class ManagedComposeTests(unittest.TestCase):
    def model(self, *files, omit=()):
        env = {key: value for key, value in os.environ.items() if not key.startswith(('MYSQL_', 'XA_', 'WECHAT_', 'DJANGO_', 'KOOK_', 'WECOM_'))}
        values = {'DJANGO_SECRET_KEY': 'compose-test-only-not-a-real-key-00000000',
                  'DJANGO_ALLOWED_HOSTS': 'api.test', 'XA_API_DOMAIN': 'api.test', 'XA_ADMIN_DOMAIN': 'admin.test',
                  'XA_SL_DOMAIN': 'sl.esports.club', 'XA_XA_DOMAIN': 'xa.esports.club',
                  'MYSQL_HOST': 'mysql.internal.test', 'MYSQL_PORT': '3307',
                  'MYSQL_USER': 'xa_test', 'MYSQL_PASSWORD': 'compose-test-only', 'MYSQL_DATABASE': 'xa_test',
                  'MYSQL_SSL_CA_FILE': '/dev/null', 'WECHAT_APPID': 'wx0000000000000000', 'WECHAT_SECRET': 'compose-test-only'}
        if 'deploy/compose/docker-compose.remote.yml' in files:
            values['MYSQL_ROOT_PASSWORD'] = 'compose-test-root-only'
        values = {key: value for key, value in values.items() if key not in omit}
        env.update(values)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env') as empty:
            command = ['docker', 'compose', '--env-file', empty.name]
            for file in files:
                command += ['-f', str(ROOT / file)]
            run = subprocess.run(command + ['config', '--format', 'json'], env=env, capture_output=True, text=True)
        if omit:
            self.assertNotEqual(run.returncode, 0, 'Missing required setting was accepted')
            return
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_no_embedded_database_or_public_backend_ports(self):
        model = self.model('deploy/compose/docker-compose.managed.yml')
        self.assertNotIn('db', model['services'])
        self.assertNotIn('mysql_data', model['volumes'])
        for name, service in model['services'].items():
            self.assertNotIn('db', service.get('depends_on', {}))
            if name != 'gateway':
                self.assertFalse(service.get('ports'))

    def test_all_backend_processes_use_identical_tls_credentials_and_migration_gate(self):
        services = self.model('deploy/compose/docker-compose.managed.yml')['services']
        expected = services['migrate']['environment']
        for name in ['web', 'celery-worker', 'celery-beat']:
            self.assertEqual(services[name]['environment'], expected)
            self.assertEqual(services[name]['depends_on']['migrate']['condition'], 'service_completed_successfully')
        for name in ['migrate', 'web', 'celery-worker', 'celery-beat']:
            self.assertEqual(services[name]['secrets'][0]['source'], 'mysql_ca')
        self.assertEqual(expected['MYSQL_HOST'], 'mysql.internal.test')
        self.assertEqual(expected['MYSQL_PORT'], '3307')
        self.assertEqual(expected['MYSQL_SSL_CA'], '/run/secrets/mysql_ca')
        self.assertEqual(services['web']['deploy']['replicas'], 2)
        self.assertEqual(services['celery-beat'].get('deploy', {}).get('replicas', 1), 1)

    def test_business_release_flags_match_existing_embedded_release(self):
        managed = self.model('deploy/compose/docker-compose.managed.yml')['services']
        embedded = self.model('deploy/compose/docker-compose.remote.yml', 'deploy/compose/docker-compose.release.yml')['services']
        for name in ['migrate', 'web', 'celery-worker', 'celery-beat']:
            for key, value in embedded[name]['environment'].items():
                if not key.startswith('MYSQL_'):
                    self.assertEqual(managed[name]['environment'][key], value, key)
        for name in ['web', 'celery-worker', 'celery-beat', 'admin-web', 'provider-h5', 'boss-web', 'gateway']:
            self.assertEqual(managed[name]['image'], embedded[name]['image'])
        self.assertIn('check_mysql_readiness', managed['migrate']['command'][-1].split('migrate_stores')[0])
        self.assertIn('migrate_stores', managed['migrate']['command'][-1])

    def test_missing_required_managed_settings_fail_during_configuration(self):
        for key in ['MYSQL_HOST', 'MYSQL_USER', 'MYSQL_PASSWORD', 'MYSQL_DATABASE', 'MYSQL_SSL_CA_FILE']:
            with self.subTest(key=key):
                self.model('deploy/compose/docker-compose.managed.yml', omit=(key,))

    def test_release_build_paths_and_three_store_entries_resolve(self):
        for files in [('deploy/compose/docker-compose.managed.yml',),
                      ('deploy/compose/docker-compose.remote.yml', 'deploy/compose/docker-compose.release.yml')]:
            with self.subTest(files=files):
                services = self.model(*files)['services']
                for name, app in [('admin-web', 'admin'), ('provider-h5', 'provider'), ('boss-web', 'boss')]:
                    build = services[name]['build']
                    self.assertEqual(Path(build['context']).resolve(), ROOT)
                    self.assertTrue((ROOT / build['dockerfile']).is_file())
                    self.assertEqual(build['args']['XA_APP'], app)
                    self.assertFalse(services[name].get('ports'))
                environment = services['web']['environment']
                for hostname in ('sl.esports.club', 'xa.esports.club', 'admin.test'):
                    self.assertIn(hostname, environment['DJANGO_ALLOWED_HOSTS'])
                mount = next(v for v in services['gateway']['volumes'] if v.get('target') == '/etc/caddy/Caddyfile')
                self.assertEqual(Path(mount['source']).resolve(), ROOT / 'deploy/Caddyfile')


if __name__ == '__main__':
    unittest.main()
