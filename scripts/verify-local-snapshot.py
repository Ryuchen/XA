"""Fingerprint a quiescent local database and its media, without exposing rows.

Run inside a one-off local backend container. This is an isolated recovery drill,
not a production backup client. Source and restored manifests must match exactly.
"""
import argparse
import hashlib
import json
import os
import re
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.db import connection

parser = argparse.ArgumentParser()
parser.add_argument('--media-root', default='/app/media')
args = parser.parse_args()
database = connection.settings_dict
assert os.environ.get('XA_LOCAL_VALIDATION') == 'true', 'Missing local opt-in'
assert connection.vendor == 'mysql' and database['HOST'] == 'db'
assert database['NAME'] == 'xa_verify' or re.fullmatch(r'xa_restore_\d{14}_[0-9a-f]{8}', database['NAME'])


def encode(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return {'bytes': value.hex()}
    raise TypeError(type(value).__name__)


tables = {}
with connection.cursor() as cursor:
    for table in sorted(connection.introspection.table_names(cursor)):
        assert re.fullmatch(r'[A-Za-z0-9_]+', table)
        cursor.execute(f'SHOW CREATE TABLE `{table}`')
        schema = cursor.fetchone()[1]
        cursor.execute(f"SHOW KEYS FROM `{table}` WHERE Key_name = 'PRIMARY'")
        keys = [row[4] for row in sorted(cursor.fetchall(), key=lambda row: row[3])]
        assert keys and all(re.fullmatch(r'[A-Za-z0-9_]+', key) for key in keys), table
        ordering = ','.join(f'`{key}`' for key in keys)
        cursor.execute(f'SELECT * FROM `{table}` ORDER BY {ordering}')
        digest, count = hashlib.sha256(), 0
        while rows := cursor.fetchmany(200):
            for row in rows:
                digest.update(json.dumps(row, default=encode, ensure_ascii=False, separators=(',', ':')).encode())
                digest.update(b'\n')
                count += 1
        tables[table] = {'rows': count, 'sha256': digest.hexdigest(),
                         'schema_sha256': hashlib.sha256(schema.encode()).hexdigest()}

root = Path(args.media_root).resolve(strict=True)
files = {}
for path in sorted(root.rglob('*')):
    assert not path.is_symlink(), 'Media symlinks are not supported'
    if not path.is_file():
        continue
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    files[path.relative_to(root).as_posix()] = {'bytes': path.stat().st_size, 'sha256': digest.hexdigest()}

print(json.dumps({'tables': tables, 'media': files}, ensure_ascii=False, sort_keys=True))
