"""Back up only xa_anniversary; preserve the newest fourteen verified dumps."""
import fcntl
import gzip
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

os.umask(0o077)
backup_root = Path('/var/backups/xa-anniversary')
directory = backup_root / 'daily'
for path in (backup_root, directory):
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.chmod(0o700)
assert directory.resolve() == Path('/var/backups/xa-anniversary/daily')
lock = open('/run/xa-anniversary-backup.lock', 'a')
os.chmod(lock.name, 0o600)
try:
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print(json.dumps({'skipped': 'backup already running'}))
    raise SystemExit(0)
name = 'db-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(4) + '.sql.gz'
target = directory / name
temporary = directory / (name + '.tmp')
command = ['docker', 'exec', 'xa-db-1', 'sh', '-c', 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --quick --no-tablespaces --set-gtid-purged=OFF --hex-blob xa_anniversary']
try:
    with tempfile.TemporaryFile() as errors, temporary.open('xb') as output:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors) as process:
            with gzip.GzipFile(fileobj=output, mode='wb', mtime=0) as compressed:
                shutil.copyfileobj(process.stdout, compressed, length=1024 * 1024)
            assert process.wait() == 0, 'Dedicated database dump failed'
    temporary.chmod(0o600)
    uncompressed_bytes = 0
    with gzip.open(temporary, 'rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            uncompressed_bytes += len(block)
    assert uncompressed_bytes > 0, 'Empty database dump'
    digest = hashlib.file_digest(temporary.open('rb'), 'sha256').hexdigest()
    os.replace(temporary, target)
    target.chmod(0o600)
    checksum = target.with_name(target.name + '.sha256')
    checksum.write_text(digest + '  ' + target.name + '\n')
    checksum.chmod(0o600)
    pattern = re.compile(r'^db-\d{8}T\d{6}Z-[0-9a-f]{8}\.sql\.gz$')
    files = sorted((path for path in directory.iterdir() if pattern.fullmatch(path.name) and path.is_file() and not path.is_symlink()), key=lambda path: path.name, reverse=True)
    for stale in files[14:]:
        assert stale.resolve().parent == directory.resolve()
        stale.unlink()
        companion = stale.with_name(stale.name + '.sha256')
        assert companion.resolve().parent == directory.resolve()
        companion.unlink(missing_ok=True)
    print(json.dumps({'database': 'xa_anniversary', 'backup': str(target), 'sha256': digest, 'gzip_verified': True, 'uncompressed_bytes': uncompressed_bytes, 'retention': 14}))
finally:
    if temporary.exists():
        assert temporary.resolve().parent == directory.resolve()
        temporary.unlink()
