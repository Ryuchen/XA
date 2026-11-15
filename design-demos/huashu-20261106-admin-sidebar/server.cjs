const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')

const root = __dirname
const args = process.argv.slice(2)
const readArg = (name, fallback) => {
  const index = args.indexOf(name)
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback
}
const port = Number(readArg('--port', process.env.PORT || 7100))
const host = readArg('--host', '127.0.0.1')
const types = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg' }
const server = http.createServer((request, response) => {
  let pathname
  try { pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname) } catch { response.writeHead(400); response.end(); return }
  const target = path.resolve(root, '.' + (pathname.endsWith('/') ? pathname + 'index.html' : pathname))
  if (!target.startsWith(root + path.sep) || !fs.existsSync(target) || !fs.statSync(target).isFile()) { response.writeHead(404); response.end('Not found'); return }
  response.writeHead(200, { 'Content-Type': types[path.extname(target)] || 'application/octet-stream', 'Cache-Control': 'no-store' })
  fs.createReadStream(target).pipe(response)
})
server.listen(port, host, () => console.log(`Sidebar design preview: http://${host}:${port}/`))
