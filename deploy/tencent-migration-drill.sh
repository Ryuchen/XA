#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
backup=${1:?verified backup directory required}
[[ "$backup" == /var/backups/xa/pre-upgrade-* ]]
restore_db=$(python3 -c 'import json,sys; result=json.load(open(sys.argv[1]+"/result.json")); assert result["verified"]; print(result["restore_database"])' "$backup")
old_image=$(cat "$backup/image")
docker run --rm -i --network xa_default --env-file "$backup/runtime.env" -e MYSQL_DATABASE="$restore_db" "$old_image" python - < scripts/fingerprint-finances.py > "$backup/finances-before.json"
docker run --rm --memory 384m --network xa_default --env-file "$backup/runtime.env" \
  -e MYSQL_DATABASE="$restore_db" -e WECHAT_MOCK_LOGIN=false \
  xa-backend:release-20260928 sh -c 'python manage.py migrate --noinput && python manage.py check_mysql_readiness' > "$backup/migration-drill.log" 2>&1
docker run --rm -i --network xa_default --env-file "$backup/runtime.env" -e MYSQL_DATABASE="$restore_db" -e WECHAT_MOCK_LOGIN=false xa-backend:release-20260928 python - < scripts/fingerprint-finances.py > "$backup/finances-after.json"
cmp "$backup/finances-before.json" "$backup/finances-after.json"
echo 'PASS historical database migration; wallet balances, frozen amounts, ledger, withdrawals and original order facts unchanged.'
tail -8 "$backup/migration-drill.log"
