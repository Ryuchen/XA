from datetime import datetime, timedelta, timezone
from pathlib import Path
import ssl
import tempfile
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
import pymysql
from pymysql.constants import CLIENT

from config.database import mysql_database
from config.mysql_tls.base import TLSConnection


class DatabaseConfigurationTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.directory = tempfile.TemporaryDirectory(prefix='xa-database-test-')
        cls.addClassCleanup(cls.directory.cleanup)
        cls.ca = Path(cls.directory.name) / 'ca.pem'
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'XA local test CA')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
                .not_valid_after(now + timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
                .sign(key, hashes.SHA256()))
        cls.ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    def managed(self, **overrides):
        return {'XA_DATABASE_MODE': 'managed', 'MYSQL_HOST': 'mysql.internal.example', 'MYSQL_USER': 'xa_app',
                'MYSQL_PASSWORD': 'fixture-password', 'MYSQL_DATABASE': 'xa_test_managed', 'MYSQL_SSL_CA': str(self.ca), **overrides}

    def test_existing_embedded_connection_retains_its_defaults(self):
        db = mysql_database({})
        self.assertEqual(db['ENGINE'], 'django.db.backends.mysql')
        self.assertEqual(db['OPTIONS'], {'charset': 'utf8mb4', 'isolation_level': 'read committed', 'connect_timeout': 10})

    def test_managed_configuration_uses_verified_tls_and_custom_address(self):
        db = mysql_database(self.managed(MYSQL_PORT='3307', MYSQL_CONNECT_TIMEOUT='7'))
        self.assertEqual(db['ENGINE'], 'config.mysql_tls')
        self.assertEqual(db['HOST'], 'mysql.internal.example')
        self.assertEqual(db['PORT'], '3307')
        options = {key: value for key, value in db['OPTIONS'].items() if key != 'isolation_level'}
        connection = TLSConnection(defer_connect=True, **options)
        self.assertTrue(connection.ctx.check_hostname)
        self.assertEqual(connection.ctx.verify_mode, ssl.CERT_REQUIRED)

    def test_managed_rejects_missing_or_local_database_target(self):
        for host in ('', 'db', 'localhost', '127.0.0.1', '::1', 'mysql://user:private@server', ' server '):
            with self.subTest(host=host), self.assertRaises(ImproperlyConfigured):
                mysql_database(self.managed(MYSQL_HOST=host))

    def test_managed_rejects_missing_or_root_credentials_without_echoing_values(self):
        for key, value in [('MYSQL_USER', 'root'), ('MYSQL_USER', ''), ('MYSQL_DATABASE', ''),
                           ('MYSQL_PASSWORD', ''), ('MYSQL_PASSWORD', 'xa_dev_password')]:
            with self.subTest(key=key), self.assertRaises(ImproperlyConfigured) as caught:
                mysql_database(self.managed(**{key: value}))
            self.assertNotIn('fixture-password', str(caught.exception))

    def test_managed_requires_valid_ca(self):
        invalid = Path(self.directory.name) / 'invalid.pem'
        invalid.write_text('not a certificate')
        for ca in ('', str(invalid), str(invalid) + '.missing', self.directory.name):
            with self.subTest(ca=ca), self.assertRaises(ImproperlyConfigured):
                mysql_database(self.managed(MYSQL_SSL_CA=ca))

    def test_mode_port_and_timeout_validation(self):
        for override in ({'XA_DATABASE_MODE': 'typo'}, {'MYSQL_PORT': '0'}, {'MYSQL_PORT': '65536'},
                         {'MYSQL_PORT': 'bad'}, {'MYSQL_CONNECT_TIMEOUT': '0'}, {'MYSQL_CONNECT_TIMEOUT': '301'}):
            with self.subTest(override=override), self.assertRaises(ImproperlyConfigured):
                mysql_database(self.managed(**override))

    def test_tls_server_downgrade_is_rejected_before_authentication(self):
        connection = TLSConnection(defer_connect=True, ssl_ca=str(self.ca), ssl_verify_cert=True, ssl_verify_identity=True)
        connection.server_capabilities = 0
        with patch.object(pymysql.connections.Connection, '_request_authentication') as authenticate:
            with self.assertRaises(pymysql.err.OperationalError):
                connection._request_authentication()
            authenticate.assert_not_called()

    def test_tls_server_continues_to_pymysql_verified_handshake(self):
        connection = TLSConnection(defer_connect=True, ssl_ca=str(self.ca), ssl_verify_cert=True, ssl_verify_identity=True)
        connection.server_capabilities = CLIENT.SSL
        with patch.object(pymysql.connections.Connection, '_request_authentication', return_value='verified') as authenticate:
            self.assertEqual(connection._request_authentication(), 'verified')
            authenticate.assert_called_once()
