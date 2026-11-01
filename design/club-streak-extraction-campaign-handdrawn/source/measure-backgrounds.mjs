import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const sharp=createRequire(path.resolve(path.dirname(process.execPath),'..','runtime.cjs'))('sharp');
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const runs=a=>{const out=[];let start=null;for(let i=0;i<=a.length;i++){if(a[i]&&start===null)start=i;if(!a[i]&&start!==null){out.push([start,i-1]);start=null;}}return out;};
for(const name of ['poster-gold','poster-teal','cover','social']){
 const file=path.join(root,'03-backgrounds',name+'.png');try{await fs.access(file);}catch{continue;}
 const m=await sharp(file).metadata(),{data,info}=await sharp(file).removeAlpha().raw().toBuffer({resolveWithObject:true});
 const avg=(y,a,b)=>{const sums=[0,0,0];for(let x=a;x<b;x++){const k=(y*info.width+x)*3;for(let j=0;j<3;j++)sums[j]+=data[k+j];}return sums.map(v=>v/(b-a));};
 const dark=[],blue=[];for(let y=0;y<info.height;y++){const c=avg(y,Math.round(info.width*.44),Math.round(info.width*.84));dark[y]=c[0]<95&&c[1]<135&&c[2]<155;blue[y]=c[2]-c[0]>15&&c[0]>120&&c[1]>160&&c[0]<240;}
 console.log(JSON.stringify({name,width:m.width,height:m.height,darkRows:runs(dark),blueRows:runs(blue)}));
 if(name.startsWith('poster'))await sharp(file).extract({left:0,top:Math.round(m.height*.53),width:m.width,height:Math.round(m.height*.45)}).png().toFile(path.join(root,'review',name+'-notes-crop.png'));
}
