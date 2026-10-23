#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
fixture=$(mktemp /tmp/xa-cloud-tests-XXXXXX.env)
trap 'rm -f "$fixture"' EXIT
docker inspect xa-db-1 | python3 -c 'import json,sys; obj=json.load(sys.stdin)[0]; env=dict(x.split("=",1) for x in obj["Config"]["Env"]); print("XA_TEST_MYSQL_USER=root\nXA_TEST_MYSQL_PASSWORD="+env["MYSQL_ROOT_PASSWORD"])' > "$fixture"
docker run --rm --memory 384m --network xa_default --env-file "$fixture" \
  -e XA_TEST_MYSQL_HOST=db -e XA_TEST_MYSQL_PORT=3306 \
  -e XA_TEST_MYSQL_DATABASE=xa_test_cloud_20260928 \
  xa-backend:release-20260928 python manage.py test --settings=config.container_test_settings --noinput > cloud-regression.log 2>&1
tail -9 cloud-regression.log
