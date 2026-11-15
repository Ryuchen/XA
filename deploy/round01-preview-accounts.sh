#!/usr/bin/env bash
# Provision two preview accounts only inside the designated round-01 stack.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd -P)"
state_dir=/var/lib/xa-round01/state
if [[ ! -d "$state_dir" || -L "$state_dir" || ! -f "$state_dir/env" || -L "$state_dir/env" ]]; then
  echo 'The designated private state directory is missing or unsafe.' >&2
  exit 1
fi
python3 - "$state_dir" <<'PY'
import os, stat, sys
p = sys.argv[1]
s = os.stat(p, follow_symlinks=False)
if s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) != 0o700:
    raise SystemExit('State directory must be owned by the caller and mode 0700')
PY
round_id="$(python3 - "$state_dir/env" <<'PY'
import re, sys
from pathlib import Path
value = Path(sys.argv[1]).read_text(encoding='ascii')
if not re.fullmatch(r'ROUND_ID=[0-9a-f]{16}\nROUND_SECRET=[0-9a-f]{64}\nROUND_ROOT_PASSWORD=[0-9a-f]{48}\nROUND_DB_PASSWORD=[0-9a-f]{48}\n', value):
    raise SystemExit('Invalid isolated environment file')
print(value.splitlines()[0].split('=', 1)[1])
PY
)"
project="xa-round01-$round_id"
compose=(env -i "PATH=$PATH" "HOME=$HOME" docker compose -p "$project"
  --env-file "$state_dir/env" -f "$repo_root/deploy/compose.round01-isolated.yml")
bash "$repo_root/deploy/round01-isolated.sh" config "$state_dir"
db_id="$("${compose[@]}" ps -q --status running db)"
web_id="$("${compose[@]}" ps -q --status running web | head -n 1)"
if [[ ! "$db_id" =~ ^[0-9a-f]{64}$ || ! "$web_id" =~ ^[0-9a-f]{64}$ ]]; then
  echo 'Dedicated database and packaged web container are not running.' >&2
  exit 1
fi
docker inspect "$db_id" "$web_id" | python3 -c '
import json, sys
items = json.load(sys.stdin)
project = sys.argv[1]
for item, service in zip(items, ("db", "web")):
    labels = item["Config"]["Labels"]
    env = dict(row.split("=", 1) for row in item["Config"]["Env"] if "=" in row)
    assert item["State"]["Running"]
    assert labels.get("com.docker.compose.project") == project
    assert labels.get("com.docker.compose.service") == service
    assert env.get("MYSQL_DATABASE") == "xa_round01" and env.get("MYSQL_USER") == "xa_round01"
    assert not item["HostConfig"].get("PortBindings")
    if service == "web":
        assert item["Config"]["Image"].startswith("xa-round01/backend:")
        assert env.get("MYSQL_HOST") == "db" and env.get("XA_LOCAL_VALIDATION") == "true"
        for key in ("WECHAT_APPID", "WECHAT_SECRET", "KOOK_BOT_TOKEN", "WECOM_DISPATCH_WEBHOOK"):
            assert not env.get(key)
        assert env.get("KOOK_ENABLED") == "false" and env.get("WECOM_DISPATCH_ENABLED") == "false"
print("Verified dedicated database and packaged web containers.")
' "$project"

credential_file="$state_dir/preview-accounts.json"
python3 - "$credential_file" "$project" <<'PY'
import json, os, secrets, stat, sys
path, project = sys.argv[1:]
try:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
except FileExistsError:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'r', encoding='utf-8') as f:
        info = os.fstat(f.fileno())
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise SystemExit('Existing credential file must be private and caller-owned')
        data = json.load(f)
    if data.get('project') != project:
        raise SystemExit('Existing credential file belongs to another project')
else:
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump({'project': project, 'accounts': {
            'sl_round01_admin': secrets.token_urlsafe(24),
            'sl_round01_provider': secrets.token_urlsafe(24),
        }}, f, ensure_ascii=False, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
PY
docker exec -i "$web_id" python -c "$(cat "$repo_root/deploy/round01-preview-accounts.py")" < "$credential_file"
echo "Credential file: $credential_file (private; no passwords printed)"
