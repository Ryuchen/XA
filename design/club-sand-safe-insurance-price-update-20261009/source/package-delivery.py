import hashlib
import json
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parent.parent
manifest_path = root / 'output' / 'package-files.json'
manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
archive = root / manifest['archive']
with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for item in manifest['files']:
        target = (root / item['file']).resolve()
        if not target.is_relative_to(root):
            raise ValueError('File outside current project')
        data = target.read_bytes()
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('File changed after final QA: ' + item['file'])
        z.writestr(item['file'], data)
    z.write(manifest_path, 'output/package-files.json')
with zipfile.ZipFile(archive) as z:
    if z.testzip() is not None:
        raise ValueError('ZIP integrity error')
    for item in manifest['files']:
        if hashlib.sha256(z.read(item['file'])).hexdigest() != item['sha256']:
            raise ValueError('ZIP differs from reviewed files: ' + item['file'])
result = {'archive': str(archive), 'bytes': archive.stat().st_size, 'fileCount': len(manifest['files']) + 1, 'status': 'every archived file matched final reviewed SHA-256'}
(root / 'output' / 'package-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
