import path from'node:path';import{execFileSync}from'node:child_process';import{fileURLToPath}from'node:url';
const here=path.dirname(fileURLToPath(import.meta.url)),project=path.resolve(process.argv[2]||path.join(here,'..')),revision=process.argv[3]||'redesign-r1';
const quote=x=>"'"+x.replaceAll("'","''")+"'";
const command='& ([scriptblock]::Create([System.IO.File]::ReadAllText('+quote(path.join(here,'pack-final.ps1'))+'))) -Project '+quote(project)+' -Revision '+quote(revision);
console.log(execFileSync('powershell.exe',['-NoProfile','-Command',command],{encoding:'utf8',maxBuffer:1024*1024}).trim());
