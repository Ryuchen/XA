import os
from unittest.mock import patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase

from orders.management.commands.seed_club_catalog import validate_import_database


class CatalogImportGuardTest(SimpleTestCase):
    def validate(self, db, marker='true', debug=False):
        with patch.dict(os.environ, {'XA_LOCAL_VALIDATION': marker}), patch(
            'orders.management.commands.seed_club_catalog.settings'
        ) as settings:
            settings.DEBUG = debug
            settings.DATABASES = {'default': db}
            validate_import_database()

    def test_only_explicit_local_mysql_target_is_allowed(self):
        db = {'ENGINE': 'django.db.backends.mysql', 'NAME': 'xa_verify',
              'HOST': 'db', 'USER': 'xa_verify'}
        self.validate(db)
        for field, value in [('NAME', 'xa_db'), ('HOST', 'production.example'),
                             ('USER', 'root'), ('ENGINE', 'django.db.backends.postgresql')]:
            with self.subTest(field=field), self.assertRaises(CommandError):
                self.validate({**db, field: value})
        with self.assertRaises(CommandError):
            self.validate(db, marker='false', debug=True)

    def test_sqlite_still_requires_debug(self):
        db = {'ENGINE': 'django.db.backends.sqlite3'}
        self.validate(db, marker='', debug=True)
        with self.assertRaises(CommandError):
            self.validate(db, debug=False)
