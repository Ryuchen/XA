import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { auditNativeResources } from './native-resource-budget.mjs';

test('combined image/audio budget rejects individually small resources', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'xa-native-media-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.writeFileSync(path.join(root, 'hero.png'), Buffer.alloc(120_000));
  fs.mkdirSync(path.join(root, 'voices'));
  fs.writeFileSync(path.join(root, 'voices', 'intro.MP3'), Buffer.alloc(100_000));
  fs.writeFileSync(path.join(root, 'page.js'), Buffer.alloc(250_000));
  const result = auditNativeResources(root);
  assert.equal(result.totalBytes, 220_000);
  assert.equal(result.files.length, 2);
  assert.equal(result.passed, false);
});

test('shipping native package retains only small icons and resolves artwork per environment', () => {
  const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  const root = path.join(repo, 'apps/boss-miniapp/native');
  const result = auditNativeResources(root);
  assert(result.passed, `Packaged media total ${result.totalBytes} exceeds ${result.limitBytes}`);
  assert(result.files.every(file => !file.path.startsWith('assets/brand/')));
  for (const page of ['home', 'mine', 'login', 'gifts']) {
    const template = fs.readFileSync(path.join(root, 'pages', page, 'index.wxml'), 'utf8');
    assert(!template.includes('/assets/brand/'), `${page} still references removed artwork`);
  }
});
