"""Create a legal-domain-checked native QA/upload project, without credentials."""
import json
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
out = root / '.tools/tencent-deploy/boss-cloud-project'
# Remove stale files only in our generated native tree. Otherwise moved artwork
# remains in an already-open QA/upload project and is packaged again.
native_out = out / 'native'
if native_out.exists():
    assert native_out.resolve().is_relative_to((root / '.tools/tencent-deploy').resolve())
    for stale in native_out.rglob('*'):
        if stale.is_file() and not (root / 'apps/boss-miniapp/native' / stale.relative_to(native_out)).is_file():
            assert stale.resolve().is_relative_to(native_out.resolve())
            stale.unlink()
shutil.copytree(root / 'apps/boss-miniapp/native', out / 'native', dirs_exist_ok=True)
project = json.loads((root / 'apps/boss-miniapp/project.config.json').read_text(encoding='utf-8'))
project['projectname'] = 'XA 腾讯云 HTTPS 验收'
(out / 'project.config.json').write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding='utf-8')
(out / 'project.private.config.json').write_text(json.dumps({'setting': {'urlCheck': True}}, indent=2), encoding='utf-8')
config = out / 'native/config.js'
config.write_text(config.read_text(encoding='utf-8').replace(
    "develop: { apiBase: 'http://127.0.0.1:18080/api', socketBase: '' }",
    "develop: { apiBase: 'https://api.xaesports.club/api', socketBase: 'wss://api.xaesports.club' }"), encoding='utf-8')
print(out)
