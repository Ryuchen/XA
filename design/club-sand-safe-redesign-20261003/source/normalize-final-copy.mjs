import fs from'node:fs/promises';import path from'node:path';const project=path.resolve(process.argv[2]),cfile=path.join(project,'content.json'),afile=path.join(project,'layout-adapter.mjs'),rfile=path.join(project,'source/render.mjs');
const c=JSON.parse(await fs.readFile(cfile,'utf8'));
c.display.typeset={
 objectiveHeadingCover:'同局'+c.display.objectiveHeading,
 objectiveResultFull:'同一局三项全满足，且成功撤离才达标',
 objectiveResultCover:'三项全满足，成功撤离才达标',
 noticeCompletion:[{text:'成功撤离',bold:true},{text:'，且'},{text:'三项目标同局完成',bold:true},{text:'，才算达标。'}],
 noticeBrokenOrderPrefix:'成功撤离且带出',
 noticeBrokenOrderSeparator:'：',
 noticeBrokenOrderResult:[{text:'炸单断连，不增保底。',bold:true}],
 noticeSafeInteraction:[{text:'激战时，陪玩须'},{text:'询问板板',bold:true},{text:'能否代摸两类保险。'}],
 coverWishWhen:'下单许愿二选一：'
};
c.verification.typesetEquivalence=[
 '三项缺一不可 + 且连接 + 同局成功撤离结果条，完整表达AND关系。',
 '炸单断连，不增保底等义对应中断连续计数、不增加保底；仅在成功撤离且带出低于300w时触发。',
 '激战时对应战斗激烈时；两类保险指目标卡中的沙色保险与常规大保险；须询问板板能否代摸，保留许可前提。'
];
await fs.writeFile(cfile,JSON.stringify(c,null,2));
let a=await fs.readFile(afile,'utf8');
a=a.replace("cover?'同局'+c.display.objectiveHeading:c.display.objectiveHeading","cover?c.display.typeset.objectiveHeadingCover:c.display.objectiveHeading");
a=a.replace("cover?'三项全满足，成功撤离才达标':'同一局三项全满足，且成功撤离才达标'","cover?c.display.typeset.objectiveResultCover:c.display.typeset.objectiveResultFull");
a=a.replace("{text:'下单许愿二选一：'}","{text:c.display.typeset.coverWishWhen}");
a=a.replace("const completion=[{text:'成功撤离',bold:true},{text:'，且'},{text:'三项目标同局完成',bold:true},{text:'，才算达标。'}];","const completion=c.display.typeset.noticeCompletion;");
a=a.replace("const proxy=[{text:'激战时，陪玩须'},{text:'询问板板',bold:true},{text:'能否代摸两类保险。'}];","const proxy=c.display.typeset.noticeSafeInteraction;");
a=a.replace("[[{text:'成功撤离且带出'},{text:'＜'+broken.loot+broken.unit,bold:true},{text:'炸单，'},{text:'断连、不增保底。',bold:true}]]","[[{text:c.display.typeset.noticeBrokenOrderPrefix},{text:'＜'+broken.loot+broken.unit,bold:true},{text:c.display.typeset.noticeBrokenOrderSeparator},...c.display.typeset.noticeBrokenOrderResult]]");
await fs.writeFile(afile,a);
let r=await fs.readFile(rfile,'utf8');r=r.replace('requestedSize:e.size','requestedSize:e.originalRequestedSize??e.size');r=r.replace("baseline,size:s*100*shrink,anchor:'start'","baseline,originalRequestedSize:r.size||e.size,size:s*100*shrink,anchor:'start'");
await fs.writeFile(rfile,r);
console.log('Final equivalent copy normalized in content; original rich-run sizes preserved for QA.');
