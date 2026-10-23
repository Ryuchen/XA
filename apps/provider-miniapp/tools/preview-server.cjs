const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const root = path.resolve(__dirname, '../dist-h5');
const port = Number(process.env.PROVIDER_PREVIEW_PORT || 18086);
const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.png': 'image/png', '.svg': 'image/svg+xml', '.json': 'application/json; charset=utf-8' };
http.createServer((req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${port}`);
  let file;
  try { file = path.resolve(root, '.' + decodeURIComponent(url.pathname)); }
  catch { res.writeHead(400); res.end(); return; }
  if (file !== root && !file.startsWith(root + path.sep)) { res.writeHead(403); res.end(); return; }
  if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(root, 'index.html');
  if (!fs.existsSync(file)) { res.writeHead(503); res.end('Build provider H5 before starting the preview.'); return; }
  res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
  fs.createReadStream(file).pipe(res);
}).listen(port, '127.0.0.1', () => process.stdout.write(`Provider preview http://127.0.0.1:${port} (API compiled separately)\n`));
