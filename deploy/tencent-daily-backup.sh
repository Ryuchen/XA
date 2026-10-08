#!/usr/bin/env bash
# Transactional SQL + media superset; no deletion, no production restore.
set -euo pipefail
umask 077
exec 9>/run/xa-daily-backup.lock
flock -n 9 || exit 0
cd /opt/xa-current
compose=(docker compose --env-file /etc/xa/release.env -f docker-compose.tencent.yml -f deploy/compose.tencent-domains.yml)
"${compose[@]}" config --quiet
mkdir -p /var/backups/xa/daily
chmod 700 /var/backups/xa/daily
backup=$(mktemp -d "/var/backups/xa/daily/run-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
docker exec xa-db-1 sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --quick --no-tablespaces --set-gtid-purged=OFF --hex-blob "$MYSQL_DATABASE"' > "$backup/database.sql"
image=$(docker inspect xa-web-1 --format '{{.Image}}')
docker run --rm --network none --read-only --mount type=volume,src=xa_backend_media,dst=/media,readonly --entrypoint tar "$image" -C /media -czf - . > "$backup/media.tar.gz"
cp /etc/xa/release.env /etc/xa/admin-access.json "$backup/"
cp docker-compose.tencent.yml manifest.json "$backup/"
cp -r deploy "$backup/"
(cd "$backup" && sha256sum database.sql media.tar.gz release.env admin-access.json docker-compose.tencent.yml manifest.json > SHA256SUMS && sha256sum -c SHA256SUMS >/dev/null)
touch "$backup/COMPLETE"
echo "PASS XA backup: $backup"
df -h /var/backups/xa
