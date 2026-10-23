#!/usr/bin/env bash
# Cold backup + restore into a NEW schema/directory; never overwrite application data.
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
compose=(docker compose -p xa-local-validation --env-file .tools/local-validation.env
  -f docker-compose.remote.yml -f deploy/compose.local-validation.yml)
if [[ -r /etc/xa-local-validation/wechat.env ]]; then
  compose+=(--env-file /etc/xa-local-validation/wechat.env -f deploy/compose.local-wechat.yml)
fi
bash deploy/local-validation.sh config
active_test=$("${compose[@]}" exec -T db sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot -N' <<'SQL'
SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='test_xa_test_container';
SQL
)
if [[ "$active_test" != 0 ]]; then
  echo 'Refusing maintenance while the regression test schema exists.' >&2
  exit 1
fi

backup_root=/var/lib/xa-local-validation/recovery
mkdir -p "$backup_root"
chmod 700 "$backup_root"
backup=$(mktemp -d "$backup_root/run-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
restore_db="xa_restore_$(date -u +%Y%m%d%H%M%S)_$(python3 -c 'import secrets; print(secrets.token_hex(4))')"
[[ "$restore_db" =~ ^xa_restore_[0-9]{14}_[0-9a-f]{8}$ ]]
maintenance=false
resume() {
  if "$maintenance"; then
    bash deploy/local-validation.sh up
  fi
}
trap resume EXIT
maintenance=true
"${compose[@]}" stop gateway web celery-worker celery-beat
"${compose[@]}" exec -T db sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --quick --skip-add-drop-table --no-tablespaces --set-gtid-purged=OFF --hex-blob xa_verify' > "$backup/database.sql"
docker run --rm --network none --read-only \
  --mount type=volume,src=xa-local-validation_backend_media,dst=/media,readonly \
  --entrypoint tar xa-local/xa-backend:validation -C /media -czf - . > "$backup/media.tar.gz"
"${compose[@]}" run --rm --no-deps -T web python - < scripts/verify-local-snapshot.py > "$backup/source.json"
(cd "$backup" && sha256sum database.sql media.tar.gz > SHA256SUMS)
bash deploy/local-validation.sh up
maintenance=false

# CREATE without IF NOT EXISTS fails on any collision, so an existing schema is never restored over.
"${compose[@]}" exec -T db sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot' <<SQL
CREATE DATABASE \`$restore_db\` CHARACTER SET utf8mb4;
GRANT ALL PRIVILEGES ON \`$restore_db\`.* TO 'xa_verify'@'%';
SQL
"${compose[@]}" exec -T db sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot "$1"' local-restore "$restore_db" < "$backup/database.sql"
(cd "$backup" && sha256sum -c SHA256SUMS)
mkdir "$backup/restored-media"
python3 - "$backup" <<'PY'
import sys, tarfile
from pathlib import Path, PurePosixPath
root = Path(sys.argv[1])
with tarfile.open(root / 'media.tar.gz', 'r:gz') as archive:
    for item in archive.getmembers():
        name = PurePosixPath(item.name)
        assert not name.is_absolute() and '..' not in name.parts
        assert item.isdir() or item.isfile(), 'Archive may contain only directories and files'
    archive.extractall(root / 'restored-media', filter='data')
PY
"${compose[@]}" run --rm --no-deps -T -e MYSQL_DATABASE="$restore_db" \
  -v "$backup/restored-media:/recovery-media:ro" web python - --media-root /recovery-media \
  < scripts/verify-local-snapshot.py > "$backup/restored.json"
python3 - "$backup" "$restore_db" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
source = json.loads((root / 'source.json').read_text())
restored = json.loads((root / 'restored.json').read_text())
assert source == restored, 'Restored schema, row fingerprints or media differ from source'
result = {'verified': True, 'tables': len(source['tables']),
          'rows': sum(item['rows'] for item in source['tables'].values()),
          'media_files': len(source['media']), 'restore_database': sys.argv[2],
          'backup_directory': str(root)}
(root / 'result.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result))
PY
echo 'PASS isolated backup recovery; application database and media volume preserved.'
