// Opt-in transport check against an explicitly named localhost draft report.
// node tools/integration-upload.cjs <isolated-accounts.json> <editable-report-id>
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const ts = require('../node_modules/typescript');

async function run() {
  const [accountsPath, rawId] = process.argv.slice(2);
  if (!accountsPath || !/^[1-9]\d*$/.test(rawId || '')) throw new Error('Provide isolated accounts and an editable report ID.');
  const accounts = JSON.parse(fs.readFileSync(accountsPath, 'utf8'));
  const endpoint = new URL(accounts.base);
  if (endpoint.protocol !== 'http:' || !['localhost', '127.0.0.1'].includes(endpoint.hostname)) throw new Error('This mutation test only runs against a localhost fixture.');
  const actor = accounts.actors.find(row => row.kind === 'provider');
  assert.ok(actor?.token, 'Local provider fixture is required');
  const api = endpoint.origin + '/api';
  const headers = { Authorization: 'Bearer ' + actor.token };
  const reports = await fetch(api + '/wallet/reports/', { headers }).then(response => response.json());
  const report = reports.data?.find(row => row.id === Number(rawId));
  assert.ok(report?.can_edit, 'The explicitly named report must still be editable');
  const uploads = [];
  const dependencies = {
    '@tarojs/taro': { __esModule: true, default: {
      // Match Taro H5's Blob conversion and multipart filename selection.
      uploadFile: async config => {
        const blob = await fetch(config.filePath).then(response => response.blob());
        const body = new FormData();
        Object.entries(config.formData).forEach(([key, value]) => body.append(key, String(value)));
        body.append(config.name, blob, config.fileName || 'file-no-extension');
        const response = await fetch(config.url, { method: 'POST', headers: config.header, body, credentials: 'omit' });
        const data = await response.text();
        uploads.push({ name: config.name, fileName: config.fileName, mime: blob.type, size: blob.size });
        return { statusCode: response.status, data };
      },
    } },
    './auth': { getSessionRevision: () => 1, getStoredToken: () => actor.token, getStoredRefreshToken: () => '', sessionChangedError: () => new Error('Session changed') },
    './orderNetwork': {}, './env': { BASE_URL: api },
  };
  const module = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/utils/request.ts'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  vm.runInNewContext(code, { module, exports: module.exports, require: name => dependencies[name], process: { env: { TARO_ENV: 'h5' } }, fetch, setTimeout, clearTimeout });
  const results = [];
  for (const [name, type, expected] of [['evidence.png', 'image/png', 0], ['evidence.jpg', 'image/jpeg', 0], ['invalid-png', 'image/png', 400]]) {
    const bytes = name === 'invalid-png' ? Buffer.from('Not a real PNG image') : fs.readFileSync(path.join(__dirname, '../tests/fixtures', name));
    const localUrl = URL.createObjectURL(new Blob([bytes], { type }));
    try {
      const response = await module.exports.uploadFile(`/wallet/reports/${rawId}/images/`, localUrl, { kind: 'ENTRY' }, 'image');
      assert.equal(response.code, expected, `${name}: ${response.msg || 'unexpected upload response'}`);
      results.push({ fixture: name, code: response.code, ...uploads.at(-1) });
    } finally { URL.revokeObjectURL(localUrl); }
  }
  process.stdout.write(JSON.stringify({ result: 'PASS', report_id: Number(rawId), cases: results }) + '\n');
}
run().catch(error => { process.stderr.write(error.message + '\n'); process.exitCode = 1; });
