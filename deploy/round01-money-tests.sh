#!/usr/bin/env bash
# Run only against the dedicated round-01 WSL Compose stack after deployment.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd -P)"
if [[ $# != 1 || ! -f "$1/env" || -L "$1/env" ]]; then
  echo 'Usage: bash deploy/round01-money-tests.sh STATE_DIR' >&2
  exit 2
fi
state_dir="$(cd "$1" && pwd -P)"
round_id="$(python3 - "$state_dir/env" <<'PY'
import re, sys
from pathlib import Path
value = Path(sys.argv[1]).read_text(encoding='ascii')
if not re.fullmatch(r'ROUND_ID=[0-9a-f]{16}\nROUND_SECRET=[0-9a-f]{64}\nROUND_ROOT_PASSWORD=[0-9a-f]{48}\nROUND_DB_PASSWORD=[0-9a-f]{48}\n', value):
    raise SystemExit('Invalid isolated state file')
print(value.splitlines()[0].split('=', 1)[1])
PY
)"
project="xa-round01-$round_id"
compose=(env -i "PATH=$PATH" "HOME=$HOME" docker compose -p "$project"
  --env-file "$state_dir/env" -f "$repo_root/deploy/compose.round01-isolated.yml")

# Verify the declared isolation before touching the dedicated MySQL service.
bash "$repo_root/deploy/round01-isolated.sh" config "$state_dir"
db_id="$("${compose[@]}" ps -q --status running db)"
web_ids="$("${compose[@]}" ps -q --status running web)"
if [[ ! "$db_id" =~ ^[0-9a-f]{64}$ || -z "$web_ids" ]]; then
  echo 'Dedicated MySQL and web services must already be running.' >&2
  exit 1
fi
docker inspect "$db_id" | python3 -c '
import json, sys
container = json.load(sys.stdin)[0]
labels = container["Config"]["Labels"]
env = dict(item.split("=", 1) for item in container["Config"]["Env"] if "=" in item)
project = sys.argv[1]
assert labels.get("com.docker.compose.project") == project
assert labels.get("com.docker.compose.service") == "db"
assert container["State"]["Running"]
assert env.get("MYSQL_DATABASE") == "xa_round01"
assert env.get("MYSQL_USER") == "xa_round01"
assert not container["HostConfig"].get("PortBindings")
print("Verified dedicated MySQL container:", container["Name"].lstrip("/"))
' "$project"

# These schemas and grants exist only inside the checked disposable MySQL.
# Backslash-escape underscores in GRANT database patterns for exact matching.
"${compose[@]}" exec -T db sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot' <<'SQL'
CREATE DATABASE IF NOT EXISTS `xa_test_round01` CHARACTER SET utf8mb4;
GRANT ALL PRIVILEGES ON `xa\_test\_round01`.* TO 'xa_round01'@'%';
GRANT ALL PRIVILEGES ON `test\_xa\_test\_round01`.* TO 'xa_round01'@'%';
SQL

"${compose[@]}" exec -T \
  -e DJANGO_SETTINGS_MODULE=config.container_test_settings \
  -e XA_TEST_MYSQL_DATABASE=xa_test_round01 \
  -e XA_TEST_MYSQL_HOST=db -e XA_TEST_MYSQL_PORT=3306 \
  -e XA_TEST_MYSQL_USER=xa_round01 web sh -c '
    export XA_TEST_MYSQL_PASSWORD="$MYSQL_PASSWORD"
    exec python manage.py test --noinput \
      orders.tests.test_dispatch_real_workflow \
      wallet.test_fund_receipts \
      wallet.test_order_income_withdraw_journey \
      wallet.test_b1_money_guards \
      wallet.test_withdraw_accounts
  '
