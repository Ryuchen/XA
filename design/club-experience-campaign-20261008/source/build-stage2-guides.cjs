const fs=require('fs'),path=require('path');
const sharp=require(process.argv[2]);
const root=path.resolve(__dirname,'..');
const hero=fs.readFileSync(path.join(root,'01-illustrations/experience-trio-20261009-r1.png')).toString('base64');
const out=path.join(root,'02-layouts');
const navy='#203f53',mint='#eaf6f0',gold='#fff3d7',ivory='#fffdf5';
const rect=(x,y,w,h,fill=ivory,stroke='#9dbccd',r=20)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}" stroke="${stroke}" stroke-width="2"/>`;
const text=(s,x,y,size=28,fill=navy)=>`<text x="${x}" y="${y}" text-anchor="middle" dominant-baseline="middle" font-family="Microsoft YaHei" font-size="${size}" fill="${fill}">${s}</text>`;
const img=(x,y,w,h)=>`<image x="${x}" y="${y}" width="${w}" height="${h}" href="data:image/png;base64,${hero}"/>`;
function draw(id,labels){let w,h,s='',regions=[];
const zone=(key,x,y,cw,ch,label,fill=ivory)=>{regions.push({containerId:key,bounds:[x,y,cw,ch],safe:[x+20,y+20,cw-40,ch-40]});s+=rect(x,y,cw,ch,fill);if(labels)s+=text(label,x+cw/2,y+ch/2,26);};
if(id==='cover'){
w=1600;h=900;s+=img(700,85,870,700);if(labels){zone('brand',80,135,560,70,'品牌 · 左对齐');zone('title',80,270,560,290,'艺术主标题 · 左对齐');}s+='<path d="M85 607 Q360 640 642 607" fill="none" stroke="#dcae69" stroke-width="7"/>';zone('claim',80,670,570,95,'核心卖点 · 居中',navy);if(labels)s+=text('平台 / 模式 · 居中',365,807,28);regions.push({containerId:'platform',bounds:[80,780,570,60],safe:[100,790,530,40]});
}else if(id==='social'){
w=1200;h=1600;s+=img(570,25,590,442);if(labels){zone('brand',60,60,485,65,'兴安电竞');zone('title',60,165,485,210,'体验单 · 标题');s+=text('新客条件说明区',600,475,28);}
for(let i=0;i<4;i++){let x=i%2?620:60,y=i<2?530:1010;let name=['端游 · 新客','手游 · 新客','端游 · 每日','手游 · 每日'][i];s+=rect(x,y,520,440);s+=rect(x,y,520,72,navy,navy,16);if(labels)s+=text(name,x+260,y+36,28,'white');for(let j=0;j<3;j++){let yy=y+85+j*112;zone(`group-${i}-row-${j}`,x+15,yy,490,100,j===0?'超值券 · 金额 / 保底':'模式 · 金额 / 保底',j===0?gold:ivory);}}
}else{
w=1200;h=2400;s+=img(555,32,600,450);if(labels){zone('brand',60,65,470,65,'兴安电竞');zone('title',60,190,470,170,'体验单 · 艺术主标题');zone('platform',60,390,410,70,id==='desktop-experience'?'端游 · 平台标识':'手游 · 平台标识');}
for(let i=0;i<2;i++){let y=i===0?540:990;s+=rect(60,y,1080,70,navy,navy,16);if(labels)s+=text(i===0?'新客专享 · UID限制':'每日一单',600,y+35,30,'white');zone(`section-${i}-super`,60,y+105,460,300,'超值券 · 模式 / 金额 / 保底',gold);zone(`section-${i}-secret`,540,y+105,290,300,'机密 · 金额 / 保底',mint);zone(`section-${i}-absolute`,850,y+105,290,300,'绝密 · 金额 / 保底',ivory);}
s+=rect(60,1455,1080,70,navy,navy,16);if(labels)s+=text('下单须知',600,1490,32,'white');zone('map',60,1560,520,410,'地图说明 · 选图与排队');zone('equipment',610,1560,530,410,'装备规范 · 房卡 / 背包 / 左轮');zone('compensation',60,2010,520,250,'炸单补偿 · 机密 / 绝密');zone('restrictions',610,2010,530,250,'活动限制');if(labels)s+=text('页脚 · 解释权',600,2320,26);
}
return {w,h,regions,svg:`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${rect(0,0,w,h,ivory,ivory,0)}${s}</svg>`};}
(async()=>{const plan={phase:'structure-and-text-free-background',pages:[]};for(const id of ['cover','social','desktop-experience','mobile-experience']){for(const labels of [false,true]){const p=draw(id,labels);const base=path.join(out,`${id}-${labels?'structure':'blank-guide'}-r1`);fs.writeFileSync(base+'.svg',p.svg);await sharp(Buffer.from(p.svg)).png().toFile(base+'.png');if(labels)plan.pages.push({id,width:p.w,height:p.h,regions:p.regions});}}fs.writeFileSync(path.join(out,'layout-plan-r1.json'),JSON.stringify(plan,null,2));console.log('Four structure diagrams and four text-free generation guides rendered.');})();
