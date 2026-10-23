import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const app = fileURLToPath(new URL('../apps/admin-web/', import.meta.url));
const vite = fileURLToPath(new URL('../apps/admin-web/node_modules/vite/bin/vite.js', import.meta.url));
for (const apiBase of ['/api', 'https://api.xaesports.club/api']) {
  test(`actual admin build rejects the provider API base ${apiBase}`, () => {
    const result = spawnSync(process.execPath, [vite, 'build'], {
      cwd: app, encoding: 'utf8', env: { ...process.env, VITE_API_BASE: apiBase },
    });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /VITE_API_BASE.*api\/admin/);
    assert.doesNotMatch(result.stdout, /transforming/);
  });
}
