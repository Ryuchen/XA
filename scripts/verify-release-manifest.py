"""Verify every reviewed source/static file; do not inspect private env files."""
import hashlib
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root/'manifest.json').read_text())
failures = []
for name, expected in manifest['files'].items():
    path = root / name
    if not path.resolve().is_relative_to(root.resolve()):
        raise SystemExit('Manifest path escaped release directory')
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        failures.append(name)
print(json.dumps({'files':len(manifest['files']), 'mismatches':failures}))
sys.exit(bool(failures))
