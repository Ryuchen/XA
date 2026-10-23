#!/usr/bin/env bash
# Local WSL/Docker validation only; never loads release or production settings.
set -euo pipefail
cd "$(dirname "$0")/.."
compose=(docker compose -p xa-local-validation --env-file .tools/local-validation.env
  -f docker-compose.remote.yml -f deploy/compose.local-validation.yml)
if [[ -r /etc/xa-local-validation/wechat.env ]]; then
  compose+=(--env-file /etc/xa-local-validation/wechat.env -f deploy/compose.local-wechat.yml)
fi

case "${1:-status}" in
  config)
    "${compose[@]}" config --quiet
    "${compose[@]}" config --format json | python3 -c '
import json, sys
config = json.load(sys.stdin)
assert config["name"] == "xa-local-validation"
services = config["services"]
assert services["web"]["environment"]["MYSQL_DATABASE"] == "xa_verify"
assert services["web"]["environment"]["XA_LOCAL_VALIDATION"] == "true"
assert services["web"]["deploy"]["replicas"] == 2
assert services["celery-beat"].get("deploy", {}).get("replicas", 1) == 1
for name, service in services.items():
    if name != "gateway":
        assert not service.get("ports"), "Unexpected port binding: " + name
bindings = services["gateway"]["ports"]
assert len(bindings) == 3
assert all(item["host_ip"] == "127.0.0.1" for item in bindings)
assert {str(item["published"]) for item in bindings} == {"18080", "18081", "18082"}
print("PASS isolated Compose database and loopback-only gateway ports")
'
    ;;
  build)
    # Separate invocations also keep BuildKit from building all three at once.
    for service in web admin-web provider-h5; do
      "${compose[@]}" build "$service"
    done
    ;;
  up)
    "${compose[@]}" up -d --no-build --wait --wait-timeout 180
    ;;
  status)
    "${compose[@]}" ps -a
    ;;
  check)
    "${compose[@]}" exec -T web python manage.py check
    "${compose[@]}" exec -T web python manage.py check_mysql_readiness
    "${compose[@]}" exec -T celery-worker celery -A config inspect ping --timeout 10
    ;;
  smoke)
    "${compose[@]}" exec -T web python - < scripts/verify-local-deployment.py
    ;;
  test|test-all)
    # Grant only two disposable schemas inside this isolated Compose MySQL.
    # Credentials remain inside the container environment and are not logged.
    "${compose[@]}" exec -T db sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot' <<'SQL'
CREATE DATABASE IF NOT EXISTS xa_test_container CHARACTER SET utf8mb4;
GRANT ALL PRIVILEGES ON xa_test_container.* TO 'xa_verify'@'%';
GRANT ALL PRIVILEGES ON test_xa_test_container.* TO 'xa_verify'@'%';
SQL
    labels=(orders.tests.test_release_flow orders.tests.test_dispatch_concurrency
      orders.tests.test_report_settlement_concurrency wallet.test_withdraw_accounts
      wallet.test_fund_receipts users.test_wechat_binding_concurrency)
    if [[ "$1" == test-all ]]; then labels=(); fi
    "${compose[@]}" exec -T -e DJANGO_SETTINGS_MODULE=config.container_test_settings \
      -e XA_TEST_MYSQL_DATABASE=xa_test_container -e XA_TEST_MYSQL_HOST=db \
      -e XA_TEST_MYSQL_PORT=3306 -e XA_TEST_MYSQL_USER=xa_verify web sh -c '
        export XA_TEST_MYSQL_PASSWORD="$MYSQL_PASSWORD"
        exec python manage.py test --noinput "$@"
      ' local-tests "${labels[@]}"
    ;;
  stop)
    "${compose[@]}" stop
    ;;
  *)
    echo 'Usage: bash deploy/local-validation.sh {config|build|up|status|check|smoke|test|test-all|stop}' >&2
    exit 2
    ;;
esac
