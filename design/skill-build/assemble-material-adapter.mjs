import fs from 'node:fs/promises';
const project='G:/Github/XA/design/club-material-campaign-v3-handdrawn';
const skill='G:/Github/XA/skills/xingan-gameplay-posters';
let src=await fs.readFile(project+'/06-typesetting/build-final.mjs','utf8');
src=src.slice(src.indexOf('const ink='),src.indexOf("await fs.writeFile(path.join(here,'text-requests.json')"));
src=src.replace("const id='t'+requests.length", "const id='t'+createHash('sha256').update(key).digest('hex').slice(0,20)");
src=src.replace("  art(p,'常规',47,153,55,{maxWidth:157,outlineWidth:3.8});\n  art(p,'物资单',45,220,76,{maxWidth:258,outlineWidth:4.8});", "  art(p,content.titleLines[0],47,153,55,{maxWidth:257,outlineWidth:3.8});\n  art(p,content.titleLines[1],45,220,76,{maxWidth:258,outlineWidth:4.8});");
src=src.replaceAll("'工作日专享'", "content.offer.when+'专享'");
src=src.replaceAll("'累计物资保底'", 'content.offer.scope');
src=src.replaceAll("'+20%'",'content.offer.increase');
src=src.replaceAll("'常规物资单'",'content.title');
src=src.replaceAll("'累计带出物资'",'content.columns.loot');
src=src.replaceAll("'订单价格'",'content.columns.price');
src=src.replaceAll("'猛攻保障'",'content.guarantee.title');
src=src.replaceAll("'成功撤离时适用'",'content.guarantee.prerequisite');
src=src.replaceAll("tx(p,'或',435",'tx(p,content.guarantee.operator,435');
src=src.replace("'表内为基础累计保底；工作日按上方活动规则执行。'",'content.tableNote');
src=src.replace("'端游 · 手游  /  绝密 · 机密'",'content.coverVersions');
src=src.replace("'累计物资保底 +20%'","content.offer.scope+' '+content.offer.increase");
src=src.replace("'端游 / 手游 · 四类价目'",'content.advertisingSubtitle');
src=src.replace("'表内为基础累计物资保底 · 工作日按上方规则加成'",'content.advertisingNote');
src=src.replace("'下单须知'",'content.rulesTitle');
src=src.replace("'物资归属 · 装备权益 · 补偿结算'",'content.rulesSubtitle');
const compStart=src.indexOf('    if(i===2){');
const compEnd=src.indexOf('    }else{',compStart);
if(compStart<0||compEnd<0)throw new Error('无法定位补偿模板');
src=src.slice(0,compStart)+`    if(r.compensation){
      r.compensation.forEach((row,j)=>{
        const lineCy=cy+(j-.5)*lineHeight;
        tx(p,row.reason,256,lineCy,13.4,{style:0,role:'rule-body'});
        tx(p,row.items[0].label,362,lineCy,13.4,{style:0,role:'rule-body'});
        number(p,'+'+row.items[0].amount,'w',485,lineCy,16,color,12,'compensation');
        tx(p,row.items[1].label,543,lineCy,13.4,{style:0,role:'rule-body'});
        number(p,'+'+row.items[1].amount,'w',714,lineCy,16,color,12,'compensation');
      });
`+src.slice(compEnd);
const guard=`import {createHash} from 'node:crypto';

// Approved material-series r2 adapter. Business values come from content.json.
// Copy this file into each campaign before adapting its layout.
export function makeLayout(content){
  if(content.tiers?.length!==4)throw new Error('此适配器仅对应四类价目；其他玩法请调整项目适配器/底图。');
  if(content.titleLines?.length!==2)throw new Error('本竖版标题需要两行 titleLines；长标题请调整标题安全区。');
  if(new Set(content.tiers.map(t=>t.id)).size!==4)throw new Error('页面 id 必须唯一');
  for(const t of content.tiers){
    if(!['gold','teal'].includes(t.color))throw new Error('本例底图色系为 gold/teal');
    if(t.prices?.length!==6||t.loot?.length!==6)throw new Error('每页需要六档；更改行数需要适配底图与布局');
    if([...t.prices,...t.loot].some(v=>!Number.isFinite(v)||v<0))throw new Error('价格/物资必须为非负数值');
    const groups=content.rules[t.mode];
    if(groups?.length!==6)throw new Error('本例为六组规则；其他结构需要调整布局');
    for(const r of groups){
      if(r.compensation){
        if(r.compensation.length!==2||r.compensation.some(row=>row.items?.length!==2))throw new Error('本例补偿区采用两行两列');
      }else if(!r.lines?.length||r.lines.length>3)throw new Error('规则正文应为1–3行；多于3行需要增加区域高度');
    }
    if(!Number.isFinite(content.guarantee.lootThreshold[t.mode]))throw new Error('缺少该模式保障门槛');
  }
`;
await fs.writeFile(skill+'/scripts/layout-material.mjs',guard+src+'\nreturn {requests,pages};\n}\n');
const content=JSON.parse(await fs.readFile(skill+'/assets/material-v3/content.example.json','utf8'));
content.provenance={kind:'historical-example',date:'2026-09-28',verified:false,note:'本轮用户明确沿用或提供更新并核对后方可采用；不是实时价格表。'};
content.titleLines=['常规','物资单'];
content.columns={price:'订单价格',loot:'累计带出物资'};
content.tableNote='表内为基础累计保底；工作日按上方活动规则执行。';
content.coverVersions='端游 · 手游  /  绝密 · 机密';
content.advertisingSubtitle='端游 / 手游 · 四类价目';
content.advertisingNote='表内为基础累计物资保底 · 工作日按上方规则加成';
content.rulesTitle='下单须知';content.rulesSubtitle='物资归属 · 装备权益 · 补偿结算';
for(const mode of ['绝密','机密']){
  const r=content.rules[mode][2],values=mode==='绝密'?[[200,300],[300,200]]:[[100,150],[150,100]];
  delete r.lines;
  r.compensation=[{reason:'应带未带',items:[{label:'金卡',amount:values[0][0]},{label:'红卡',amount:values[0][1]}]},
    {reason:'未经同意未带',items:[{label:'背包',amount:values[1][0]},{label:'左轮',amount:values[1][1]}]}];
}
await fs.writeFile(skill+'/assets/material-v3/content.example.json',JSON.stringify(content,null,2));
console.log('Prepared standalone material adapter and historical example.');
