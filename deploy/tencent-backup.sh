#!/usr/bin/env bash
# Run as root, before replacing the existing xa containers. No volume deletion.
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
backup=$(mktemp -d "/var/backups/xa/pre-upgrade-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
docker inspect xa-web-1 > "$backup/web-inspect.json"
docker inspect xa-db-1 > "$backup/db-inspect.json"
python3 - "$backup" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
web = json.loads((p/'web-inspect.json').read_text())[0]
db = json.loads((p/'db-inspect.json').read_text())[0]
assert any(m.get('Name') == 'xa_mysql_data' and m['Destination'] == '/var/lib/mysql' for m in db['Mounts'])
assert any(m.get('Name') == 'xa_backend_media' and m['Destination'] == '/app/media' for m in web['Mounts'])
env = dict(line.split('=', 1) for line in web['Config']['Env'])
assert env['MYSQL_DATABASE'] == 'xa_db'
(p/'runtime.env').write_text('\n'.join(f'{k}={v}' for k,v in env.items())+'\n')
(p/'image').write_text(web['Image'])
PY
cp /opt/xa/.env.remote "$backup/old.env.remote"
cp /opt/xa/docker-compose.remote.yml "$backup/old-compose.yml"
image=$(cat "$backup/image")
services=(xa-web-1 xa-celery-worker-1 xa-celery-beat-1 xa-admin-web-1 xa-provider-h5-1)
maintenance=false
resume() { if "$maintenance"; then docker start "${services[@]}" >/dev/null; fi; }
trap resume EXIT
maintenance=true
docker stop "${services[@]}" >/dev/null
docker exec xa-db-1 sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --quick --no-tablespaces --set-gtid-purged=OFF --hex-blob "$MYSQL_DATABASE"' > "$backup/database.sql"
docker run --rm --network none --read-only -v xa_backend_media:/media:ro --entrypoint tar "$image" -C /media -czf - . > "$backup/media.tar.gz"
docker run --rm -i --network xa_default --env-file "$backup/runtime.env" -v xa_backend_media:/app/media:ro "$image" python - --database xa_db < scripts/fingerprint-deployment.py > "$backup/source.json"
(cd "$backup" && sha256sum database.sql media.tar.gz > SHA256SUMS)
restore_db="xa_restore_$(date -u +%Y%m%d%H%M%S)_$(python3 -c 'import secrets; print(secrets.token_hex(4))')"
docker exec -i xa-db-1 sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot' <<SQL
CREATE DATABASE \`$restore_db\` CHARACTER SET utf8mb4;
SQL
docker exec -i xa-db-1 sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot "$1"' restore "$restore_db" < "$backup/database.sql"
mkdir "$backup/restored-media"
tar -xzf "$backup/media.tar.gz" -C "$backup/restored-media"
docker run --rm -i --network xa_default --env-file "$backup/runtime.env" -e MYSQL_DATABASE="$restore_db" -v "$backup/restored-media:/app/media:ro" "$image" python - --database "$restore_db" < scripts/fingerprint-deployment.py > "$backup/restored.json"
python3 - "$backup" "$restore_db" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
source=json.loads((p/'source.json').read_text())
assert source == json.loads((p/'restored.json').read_text()), 'Restore differs from source'
result={'verified':True,'tables':len(source['tables']),
        'rows':sum(t['rows'] for t in source['tables'].values()),
        'media_files':len(source['media']),'backup':str(p),'restore_database':sys.argv[2]}
(p/'result.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result))
PY
(cd "$backup" && sha256sum -c SHA256SUMS)
docker start "${services[@]}" >/dev/null
maintenance=false
echo 'PASS backup and isolated restore; existing data preserved.'
