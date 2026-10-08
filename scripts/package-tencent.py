"""Package the reviewed working tree and built assets, excluding local secrets."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
out = root/'.tools/tencent-deploy'
out.mkdir(parents=True, exist_ok=True)
exclude = {'node_modules', '.git', '__pycache__', 'media', 'staticfiles', 'venv',
           'dist', 'dist-h5', '.swc', '.vite'}
files = {}
for directory in ('services/backend', 'deploy', 'scripts', 'packages',
                  'apps/admin-web', 'apps/provider-miniapp', 'apps/boss-miniapp'):
    for path in (root/directory).rglob('*'):
        relative = path.relative_to(root)
        if (not path.is_file() or any(part in exclude for part in relative.parts)
                or (path.name.startswith('.env') and path.name != '.env.example')
                or path.suffix in {'.log', '.sqlite3', '.pem', '.pyc', '.dpapi', '.tsbuildinfo'}):
            continue
        files[relative.as_posix()] = path
for name in ('docker-compose.tencent.yml', 'docker-compose.remote.yml',
             'docker-compose.release.yml', 'package.json', 'pnpm-lock.yaml', 'pnpm-workspace.yaml',
             'docs/TENCENT_DEPLOYMENT_20260928.md', 'docs/TENCENT_DEPLOYMENT_PLAN.md',
             'docs/RELEASE_READINESS.md', 'docs/MINIAPP_MEDIA_BUDGET_20260928.md', 'docs/KOOK_ADMIN_SETUP.md', 'docs/WECOM_CUSTOMER_SERVICE_SETUP.md'):
    files[name] = root/name
for app, artifact in (('admin-web/dist','admin'), ('provider-miniapp/dist-h5','provider')):
    assert (root/'apps'/app/'index.html').is_file(), f'Missing build: {app}'
    for path in (root/'apps'/app).rglob('*'):
        if path.is_file():
            files[f'artifacts/{artifact}/'+path.relative_to(root/'apps'/app).as_posix()] = path
manifest = {'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),
            'working_tree_snapshot':True,
            'files':{name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in sorted(files.items())}}
(out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
with tarfile.open(out/'release.tar.gz', 'w:gz') as archive:
    for name, path in sorted(files.items()):
        archive.add(path, arcname=name, recursive=False)
    archive.add(out/'manifest.json',arcname='manifest.json')
archive_hash=hashlib.sha256((out/'release.tar.gz').read_bytes()).hexdigest()
(out/'release.sha256').write_text(archive_hash+'  release.tar.gz\n')
preview=out/'boss-ip-project'
native_preview=preview/'native'
if native_preview.exists():
    assert native_preview.resolve().is_relative_to(out.resolve())
    for stale in native_preview.rglob('*'):
        if stale.is_file() and not (root/'apps/boss-miniapp/native'/stale.relative_to(native_preview)).is_file():
            assert stale.resolve().is_relative_to(native_preview.resolve())
            stale.unlink()
shutil.copytree(root/'apps/boss-miniapp/native',preview/'native',dirs_exist_ok=True)
project=json.loads((root/'apps/boss-miniapp/project.config.json').read_text(encoding='utf-8'))
project['projectname']='XA 腾讯云 IP 开发联调'
(preview/'project.config.json').write_text(json.dumps(project,ensure_ascii=False,indent=2),encoding='utf-8')
(preview/'project.private.config.json').write_text(json.dumps({'setting':{'urlCheck':False}},indent=2))
config=preview/'native/config.js'
config.write_text(config.read_text(encoding='utf-8').replace('http://127.0.0.1:18080/api','http://43.167.213.74/api'),encoding='utf-8')
print(json.dumps({'files':len(files),'archive_bytes':(out/'release.tar.gz').stat().st_size,'sha256':archive_hash,'ip_preview':str(preview)}))
