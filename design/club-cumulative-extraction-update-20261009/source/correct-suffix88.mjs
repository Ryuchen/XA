import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
for(const f of ['source/update-desktop-guarantees.mjs','source/review.mjs','source/finalize.mjs']){
 let text=await fs.readFile(path.join(root,f),'utf8');
 text=text.replaceAll('888','88').replaceAll('保留千位','保留百位及更高位').replaceAll('后三位','后两位').replaceAll('retain-thousands-replace-last-three-with-88','retain-hundreds-replace-last-two-with-88').replaceAll('cumulative-r1','cumulative-r2');
 if(f.endsWith('update-desktop-guarantees.mjs'))text=text.replace('Math.floor(discountedW/1000)*1000+88','Math.floor(discountedW/100)*100+88');
 await fs.writeFile(path.join(root,f),text);
}
const read=async f=>JSON.parse((await fs.readFile(path.join(root,f),'utf8')).replace(/^\uFEFF/,''));
const content=await read('content.json'),state=await read('campaign-state.json');
content.contentNotes=content.contentNotes.map(s=>s.startsWith('手游价格与保底取本轮截图')?'手游价格与保底取本轮截图；端游保底按对应模式常规物资单同价线性折算值的七折，保留百位及更高位，后两位改88。':s);
state.revision='cumulative-r2';state.stage='vector-production';state.visualQA.status='pending';
state.latestCorrection='用户说明888是笔误，应仅保留后两位88。算法仍为同模式常规档位线性折算后乘70%，保留百位及更高位，后两位改88；手游不变。';
for(const [f,v]of [['content.json',content],['campaign-state.json',state]])await fs.writeFile(path.join(root,f),JSON.stringify(v,null,2));
console.log('Corrected suffix calculation to floor(value / 100) * 100 + 88.');
