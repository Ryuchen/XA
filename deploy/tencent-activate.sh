#!/usr/bin/env bash
# Execute only after the recorded regression and migration drill have passed.
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
grep -Eq '^OK( \(skipped=[0-9]+\))?$' cloud-regression.log
[[ -r /etc/xa/release.env ]]
compose=(docker compose --env-file /etc/xa/release.env -f docker-compose.tencent.yml -f deploy/compose.tencent-domains.yml)
"${compose[@]}" config --quiet
"${compose[@]}" run --rm --no-deps -T gateway caddy validate --config /etc/caddy/Caddyfile
# Capture money facts while all old writers are stopped. Do not remove volumes.
docker stop xa-web-1 xa-celery-worker-1 xa-celery-beat-1 xa-admin-web-1 xa-provider-h5-1 >/dev/null
backup=/var/backups/xa/pre-upgrade-20260928T082754Z-ZyOTIl
old_image=$(cat "$backup/image")
docker run --rm -i --network xa_default --env-file "$backup/runtime.env" "$old_image" python - < scripts/fingerprint-finances.py > finances-before-live.json
docker exec xa-db-1 sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --quick --no-tablespaces --set-gtid-purged=OFF --hex-blob "$MYSQL_DATABASE"' > "$backup/database-before-activation.sql"
# Keep scheduled writers stopped until the financial comparison is complete.
"${compose[@]}" up -d --remove-orphans --wait --wait-timeout 240 db redis web admin-web provider-h5 gateway
"${compose[@]}" exec -T --index 1 web python manage.py check
"${compose[@]}" exec -T --index 1 web python manage.py check_mysql_readiness
"${compose[@]}" exec -T --index 1 web python - < scripts/fingerprint-finances.py > finances-after-live.json
cmp finances-before-live.json finances-after-live.json
"${compose[@]}" up -d --wait --wait-timeout 120 celery-worker celery-beat
python3 - <<'PY'
import json, secrets, subprocess
from pathlib import Path
credentials=Path('/etc/xa/admin-access.json')
if not credentials.exists():
    username='xa_deploy_admin'
    password=secrets.token_urlsafe(24)
    subprocess.run(['docker','compose','--env-file','/etc/xa/release.env','-f','docker-compose.tencent.yml','-f','deploy/compose.tencent-domains.yml',
                    'exec','-T','--index','1','web','python','manage.py','bootstrap_console','--username',username],
                   input=(password+'\n'+password+'\n').encode(),check=True)
    credentials.write_text(json.dumps({'url':'https://admin.xaesports.club','username':username,'password':password},indent=2))
    credentials.chmod(0o600)
    print('New deployment administrator created; credentials in /etc/xa/admin-access.json (600). Existing administrators unchanged.')
PY
ln -sfn /opt/xa-upgrade-20260928 /opt/xa-current
"${compose[@]}" ps
echo 'PASS production migration and original financial data comparison.'
