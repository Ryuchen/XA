#!/usr/bin/env bash
# Dedicated WSL acceptance deployment; never reads the existing validation env.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd -P)"
command_name="${1:-}"
if [[ "$command_name" == init ]]; then
  umask 077
  state_root="${XA_ROUND01_STATE_ROOT:-$HOME/.local/share/xa-round01}"
  mkdir -p -m 700 "$state_root"
  state_dir="$(mktemp -d "$state_root/state.XXXXXXXX")"
  python3 - "$state_dir/env" <<'PY'
import secrets, sys
from pathlib import Path
values = {
    'ROUND_ID': secrets.token_hex(8),
    'ROUND_SECRET': secrets.token_hex(32),
    'ROUND_ROOT_PASSWORD': secrets.token_hex(24),
    'ROUND_DB_PASSWORD': secrets.token_hex(24),
}
Path(sys.argv[1]).write_text(''.join(f'{k}={v}\n' for k, v in values.items()), encoding='ascii')
PY
  printf 'Isolated state directory: %s\n' "$state_dir"
  printf 'Keep this private directory for subsequent config/build/up/check commands.\n'
  exit 0
fi

if [[ $# != 2 || ! -f "$2/env" || -L "$2/env" ]]; then
  echo 'Usage: round01-isolated.sh init | {config|build|up|status|check} STATE_DIR' >&2
  exit 2
fi
state_dir="$(cd "$2" && pwd -P)"
round_id="$(python3 - "$state_dir/env" <<'PY'
import re, sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding='ascii')
assert re.fullmatch(r'ROUND_ID=[0-9a-f]{16}\nROUND_SECRET=[0-9a-f]{64}\nROUND_ROOT_PASSWORD=[0-9a-f]{48}\nROUND_DB_PASSWORD=[0-9a-f]{48}\n', text)
print(text.splitlines()[0].split('=', 1)[1])
PY
)"
project="xa-round01-$round_id"
# Do not inherit Compose overrides, credentials, or external integration settings.
compose=(env -i "PATH=$PATH" "HOME=$HOME" docker compose -p "$project"
  --env-file "$state_dir/env" -f "$repo_root/deploy/compose.round01-isolated.yml")

verify_config() {
  "${compose[@]}" config --format json | python3 -c '
import json, sys
c = json.load(sys.stdin)
s = c["services"]
assert c["name"].startswith("xa-round01-")
for name in ("migrate", "web", "celery-worker", "celery-beat"):
    e = s[name]["environment"]
    assert e["MYSQL_HOST"] == "db" and e["MYSQL_DATABASE"] == "xa_round01"
    assert e["REDIS_HOST"] == "redis" and e["XA_LOCAL_VALIDATION"] == "true"
    for key in ("WECHAT_APPID", "WECHAT_SECRET", "KOOK_BOT_TOKEN", "KOOK_DISPATCH_CHANNEL_ID", "WECOM_DISPATCH_WEBHOOK"):
        assert not e[key], (name, key)
    for key in ("WECHAT_MOCK_LOGIN", "KOOK_ENABLED", "WECOM_DISPATCH_ENABLED"):
        assert e[key] == "false", (name, key)
for name, service in s.items():
    if name != "gateway":
        assert not service.get("ports"), name
ports = s["gateway"]["ports"]
assert len(ports) == 3 and all(p["host_ip"] == "127.0.0.1" for p in ports)
assert {str(p["published"]) for p in ports} == {"28080", "28081", "28082"}
assert s["web"]["deploy"]["replicas"] == 2
for name in ("web", "admin-web", "provider-h5"):
    assert s[name]["image"].startswith("xa-round01/")
assert not any(v.get("external", False) or v.get("name", "").startswith("xa-local-validation") for v in c["volumes"].values())
print("PASS isolated Compose configuration; no credentials printed")
'
}

case "$command_name" in
  config) verify_config ;;
  build)
    verify_config
    for service in web admin-web provider-h5; do "${compose[@]}" build "$service"; done
    ;;
  up)
    verify_config
    "${compose[@]}" up -d --no-build --wait --wait-timeout 180
    ;;
  status) "${compose[@]}" ps -a ;;
  check)
    verify_config
    "${compose[@]}" exec -T web python manage.py check
    "${compose[@]}" exec -T web python manage.py check_mysql_readiness
    "${compose[@]}" exec -T celery-worker celery -A config inspect ping --timeout 10
    ;;
  *) echo 'Unknown command' >&2; exit 2 ;;
esac
