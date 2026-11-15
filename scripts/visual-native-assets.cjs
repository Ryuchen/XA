// The simulator visual review may load the project's existing brand artwork.
// Every API request and every write is rejected; this cannot forward to production.
const http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve('services/backend/engagement/static/miniapp/brand');
http.createServer((req,res)=>{
 const match=(req.url||'').match(/^\/static\/miniapp\/brand\/([\w.-]+\.(?:png|webp))$/);
 if(req.method!=='GET'||!match){res.writeHead(503,{'Content-Type':'application/json'}).end('{"code":503,"msg":"隔离视觉检查：接口不可用"}');return}
 fs.readFile(path.join(root,match[1]),(error,bytes)=>{if(error){res.writeHead(404).end();return}res.writeHead(200,{'Content-Type':match[1].endsWith('.webp')?'image/webp':'image/png','Cache-Control':'no-store'});res.end(bytes)});
}).listen(16247,'127.0.0.1',()=>console.log('Isolated brand assets: http://127.0.0.1:16247/'));
