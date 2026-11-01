import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
const root=path.resolve(import.meta.dirname,'..'),sharp=createRequire(import.meta.url)(process.argv[2]+'/sharp');
const read=f=>fs.readFileSync(root+'/'+f),write=(f,b)=>fs.writeFileSync(root+'/'+f,b);
const plan=JSON.parse(read('layout-final-plan.json')),manifest=JSON.parse(read('output/manifest.json'));
for(const [id,block,suffix] of [['01-solo-duo-price','settlement','condition-tags'],['04-boost-price','compensation','surcharge'],['06-overseas-price','surcharge','surcharge'],['07-day-price','companion','and-condition'],['09-placement-price','compensation','or-choice'],['09-placement-advert','offer','discount'],['10-maintenance-price','offers','discount']]){
 const p=plan.pages.find(p=>p.id===id),b=p.blocks.find(b=>b.id===block)||p.blocks.find(b=>b.id==='guarantee'),m=manifest.find(m=>m.id===id);
 if(!b){if(id==='01-solo-duo-price')continue;throw Error('Missing review block: '+id+'/'+block);}
 const scale=m.width/p.w,left=Math.floor(b.x*scale),top=Math.floor(b.y*scale),width=Math.ceil(b.w*scale),height=Math.ceil(b.h*scale);
 const cropped=await sharp(read(m.png),{limitInputPixels:false}).extract({left,top,width,height}).png().toBuffer();write('review/final/'+id+'-r3-'+suffix+'.png',cropped);
 write('review/final/'+id+'-r3-'+suffix+'-phone.png',await sharp(cropped).resize({width:720}).png().toBuffer());
}
const id='09-placement-advert';
const bg=Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="840" height="606"><rect width="100%" height="100%" fill="#eef3f1"/><g font-family="Microsoft YaHei UI" font-size="22" fill="#17334b" text-anchor="middle"><text x="215" y="35">展示增强前</text><text x="625" y="35">展示增强后</text></g></svg>');
write('review/presentation-comparison-r3.png',await sharp(bg).composite(['before-r3','final'].map((folder,i)=>({input:read('review/'+folder+'/'+id+'-390.png'),left:20+i*410,top:60}))).png().toBuffer());
console.log('Created source-resolution feature, logic and offer crops plus actual-PNG comparison.');
