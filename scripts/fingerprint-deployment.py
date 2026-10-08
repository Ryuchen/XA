"""Fingerprint a stopped deployment or isolated restore; never print row data."""
import argparse
import hashlib
import json
import os
import re
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--database', required=True)
parser.add_argument('--media-root', default='/app/media')
args = parser.parse_args()
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.db import connection
assert connection.vendor == 'mysql'
assert connection.settings_dict['NAME'] == args.database
assert re.fullmatch(r'[A-Za-z0-9_]+', args.database)


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
        assert keys and all(re.fullmatch(r'[A-Za-z0-9_]+', key) for key in keys)
        cursor.execute(f'SELECT * FROM `{table}` ORDER BY ' + ','.join(f'`{key}`' for key in keys))
        digest, count = hashlib.sha256(), 0
        while rows := cursor.fetchmany(200):
            for row in rows:
                digest.update(json.dumps(row, default=encode, ensure_ascii=False,
                                         separators=(',', ':')).encode() + b'\n')
                count += 1
        tables[table] = {'rows': count, 'sha256': digest.hexdigest(),
                         'schema_sha256': hashlib.sha256(schema.encode()).hexdigest()}

root = Path(args.media_root).resolve(strict=True)
files = {}
for path in sorted(root.rglob('*')):
    assert not path.is_symlink(), 'Media symlinks are not supported'
    if path.is_file():
        with path.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        files[path.relative_to(root).as_posix()] = {'bytes': path.stat().st_size, 'sha256': digest}
print(json.dumps({'tables': tables, 'media': files}, ensure_ascii=False, sort_keys=True))
