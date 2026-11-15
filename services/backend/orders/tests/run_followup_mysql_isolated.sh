#!/usr/bin/env bash
set -euo pipefail

if [[ "${XA_FOLLOWUP_MYSQL_ISOLATED:-}" != "1" ]]; then
    echo 'Requires XA_FOLLOWUP_MYSQL_ISOLATED=1' >&2
    exit 1
fi

run_id=$(python3 -c 'import secrets; print(secrets.token_hex(6))')
server="xa-followup-mysql-${run_id}"
network="xa-followup-net-${run_id}"
source_dir=$(cd "$(dirname "$0")/../.." && pwd -P)
test_targets=("$@")
if (( ${#test_targets[@]} == 0 )); then
    test_targets=(orders.tests.test_ordinary_addon_negotiated_report.OrdinaryAddonFollowupMySQLRaceTest)
fi
container_id=''
network_id=''

cleanup() {
    if [[ "$container_id" =~ ^[0-9a-f]{64}$ ]]; then
        label=$(docker inspect --format '{{index .Config.Labels "xa-followup-test"}}' "$container_id" 2>/dev/null || true)
        if [[ "$label" == "$run_id" ]]; then
            docker stop "$container_id" >/dev/null
        fi
    fi
    if [[ "$network_id" =~ ^[0-9a-f]{64}$ ]]; then
        label=$(docker network inspect --format '{{index .Labels "xa-followup-test"}}' "$network_id" 2>/dev/null || true)
        if [[ "$label" == "$run_id" ]]; then
            docker network rm "$network_id" >/dev/null
        fi
    fi
}
trap cleanup EXIT

network_id=$(docker network create --label "xa-followup-test=$run_id" "$network")
[[ "$network_id" =~ ^[0-9a-f]{64}$ ]]
container_id=$(docker run -d --rm --pull never --name "$server" --hostname "$server" --network "$network" \
    --label "xa-followup-test=$run_id" --tmpfs /var/lib/mysql:rw,size=1g --memory 1g \
    -e MYSQL_ALLOW_EMPTY_PASSWORD=yes -e MYSQL_ROOT_HOST=% mysql:8.0 \
    --innodb-buffer-pool-size=128M --max-connections=30)
[[ "$container_id" =~ ^[0-9a-f]{64}$ ]]
echo 'Waiting for disposable MySQL (no published port)'
ready=0
for _ in $(seq 1 90); do
    if docker exec "$container_id" mysqladmin -uroot -h127.0.0.1 --protocol=TCP ping --silent >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 1
done
[[ "$ready" == '1' ]]
echo 'Running isolated migrations and follow-up refund and funding tests'
docker run --rm --network "$network" -v "$source_dir:/app" -w /app \
    -e XA_FOLLOWUP_MYSQL_ISOLATED=1 -e "XA_FOLLOWUP_MYSQL_RUN_ID=$run_id" \
    -e "XA_FOLLOWUP_MYSQL_HOST=$server" -e DJANGO_SETTINGS_MODULE=config.ordinary_followup_mysql_test_settings \
    xa-local/xa-backend:validation sh -c 'pip -q install mutagen && python manage.py test --noinput "$@"' sh "${test_targets[@]}"
