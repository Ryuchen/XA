import {createHash} from 'node:crypto';
export function makeLayout(c){
 const ink='#203f53',muted='#557184',mint='#187b75',gold='#a96725',white='#fffdf6',CN='MiSans VF',NUM='MiSans VF Bold',ART='Noto Sans SC Black';
 const requests=[],keys=new Map(),pages=[];
 const req=(text,font=CN,style=1)=>{text=String(text);if(font==='MiSans VF'){font=style===1?'MiSans VF Semibold':'MiSans VF Regular';style=0;}else if(font.startsWith('MiSans VF ')){style=0;}const key=JSON.stringify([text,font,style]);if(!keys.has(key)){const id='t'+createHash('sha256').update(key).digest('hex').slice(0,20);keys.set(key,id);requests.push({id,text,font,style});}return keys.get(key);};
 const page=(id,label,bg,w,h,ew,eh,platforms)=>{let p={id,label,bg,w,h,ew,eh,platforms,elements:[],containers:[]};pages.push(p);return p;};
 const tx=(p,text,x,cy,size=30,opt={})=>{const {font=CN,bold=true,fill=ink,anchor='start',...rest}=opt;p.elements.push({type:'text',text:String(text),id:req(text,font,bold?1:0),x,cy,size,fill,anchor,...rest});};
 const art=(p,text,x,cy,size,maxWidth,group)=>p.elements.push({type:'art',text,ids:[...text].map(t=>req(t,ART,0)),x,cy,size,maxWidth,fill:ink,shadow:'#afcadb',outline:white,outlineWidth:2.6,angles:true,group});
 const raw=(p,markup)=>p.elements.push({type:'raw',markup});
 const rich=(p,runs,x,cy,size,group,opt={})=>p.elements.push({type:'rich',runs:runs.map(r=>({text:r.text,id:req(r.text,r.font||CN,r.bold?1:0),fill:r.fill||ink,size:r.size||size,bold:!!r.bold,gap:r.gap})),x,cy,size,fill:ink,group,role:'rule-body',...opt});
 const box=(p,id,bounds,safe,centered=true)=>p.containers.push({id,bounds,safe,centered});
 function brand(p,x,cy,size){raw(p,`<path d="M${x} ${cy+size*.43}l${size*.47} ${-size*.9}l${size*.48} ${size*.9}z M${x+size*.27} ${cy+size*.27}l${size*.20} ${-size*.39}l${size*.21} ${size*.39}z" fill="none" stroke="${ink}" stroke-width="${size*.075}" stroke-linejoin="round"/>`);tx(p,c.brand,x+size*1.22,cy,size,{role:'brand',group:'brand'});}
 function num(p,value,unit,ux,cy,size,color,group){tx(p,value,ux-6,cy,size,{font:NUM,fill:color,anchor:'end',group,lineGroup:group,role:unit==='r'?'price-number':'loot-number'});tx(p,unit,ux,cy,size*.94,{font:NUM,fill:color,group,lineGroup:group,role:unit==='r'?'price-unit':'loot-unit'});}
 const cover=page('cover','兴安电竞 · 体验单封面','experience-cover.png',1672,941,3200,1801,['desktop','mobile']);
 brand(cover,83.7,155,45);art(cover,'超值',82,307,142,450,'headline');art(cover,'体验单',82,492,163,494,'headline');
 tx(cover,c.game,82,605,30,{bold:false,fill:muted,group:'game',role:'supporting-copy'});
 rich(cover,[{text:'新客专享',bold:true,fill:'#f8dda7'},{text:' · ',fill:white,gap:20},{text:'每日一单',bold:true,fill:white,gap:20}],367.5,738.5,45,'claim',{anchor:'middle'});
 rich(cover,[{text:'端游 / 手游',bold:true,size:32},{text:' · ',fill:gold,size:30,gap:16},{text:'机密 / 绝密',size:30,gap:16}],367.5,819.5,32,'platform',{anchor:'middle'});
 box(cover,'game',[70,583,540,39],[82,588,515,34],false);
 box(cover,'brand',[70,115,530,90],[80,125,485,70]);box(cover,'headline',[60,207,545,400],[80,215,515,380]);box(cover,'claim',[70,693,599,91],[90,707,555,63]);box(cover,'platform',[80,790,560,56],[100,802,535,35]);
 const social=page('social','兴安电竞 · 体验单广告页','experience-social-r3.png',1086,1448,2400,3200,['desktop','mobile']);
 brand(social,83,126,29);art(social,'超值',82,204,69,310,'headline');art(social,'体验单',82,284,91,340,'headline');
 rich(social,[{text:'新客专享',bold:true,size:29},{text:' · 同一个三角洲UID',size:29,gap:10},{text:'仅可下一次',bold:true,size:29,fill:gold,gap:10}],543,442,29,'condition',{anchor:'middle'});
 box(social,'brand',[65,90,405,75],[75,105,385,55]);box(social,'headline',[60,159,410,174],[75,165,385,170]);box(social,'condition',[30,416,1025,52],[80,425,925,35]);
 for(let i=0;i<4;i++){
  const tier=c.tiers[i%2],section=tier.sections[i<2?0:1],dx=i%2?512:0,headerY=i<2?523:920,ys=i<2?[608,703,797]:[1005,1099,1190];
  rich(social,[{text:tier.platform,bold:true,size:34,fill:white},{text:' / ',size:26,fill:'#abc7d4',gap:14},{text:section.title,bold:true,size:30,fill:'#f8dda7',gap:14}],289+dx,headerY,32,`header-${i}`,{anchor:'middle'});
  box(social,`header-${i}`,[54+dx,headerY-34,470,68],[72+dx,headerY-25,434,50]);
  section.offers.forEach((o,j)=>{
   const y=ys[j],color=o.mode==='机密'?mint:gold,group=`offer-${i}-${j}`;
   if(o.superValue){raw(social,`<rect x="${81.7+dx}" y="${y-32}" width="79" height="29" rx="6" fill="${gold}"/>`);tx(social,'超值券',86.7+dx,y-17.5,23,{fill:white,group});tx(social,o.mode,86.7+dx,y+17,25,{fill:color,group});}else tx(social,o.mode,86.7+dx,y,29,{fill:color,group});
   raw(social,`<path d="M${277+dx} ${y-24}v48" stroke="#bdd4da" stroke-width="1.1"/>`);
   num(social,o.price,'r',250.7+dx,y,38,ink,group+'-price');tx(social,'保',292.7+dx,y,24,{bold:false,group,lineGroup:group+'-loot'});num(social,o.guarantee,'w',450.7+dx,y,46,color,group+'-loot');
   box(social,group,[62+dx,y-42,452,84],[80+dx,y-33,417,66]);
  });
 }
 for(const tier of c.tiers){
  const p=page(tier.id,'兴安电竞 · '+tier.platform+'体验单','experience-price.png',887,1774,2400,4800,[tier.platform==='端游'?'desktop':'mobile']);
  brand(p,65,88,27);art(p,'体验单',63,167,79,280,'headline');rich(p,[{text:c.game,size:26},{text:' / ',size:24,fill:muted,gap:12},{text:tier.platform,bold:true,size:30,fill:mint,gap:12}],217.5,230,27,'subtitle',{anchor:'middle'});
  box(p,'brand',[52,60,325,50],[62,69,305,39]);box(p,'headline',[50,118,330,96],[60,120,312,96]);box(p,'subtitle',[60,208,315,42],[65,212,305,36]);
  tier.sections.forEach((sec,i)=>{
   const hy=i===0?414:730,ty=i===0?494:810,offset=i*316;
   tx(p,String(i+1).padStart(2,'0'),82,hy,25,{fill:'#f8dda7',anchor:'middle',group:`header-${i}`});
   raw(p,`<path d="M105 ${hy-16}v32" stroke="#abc7d4" stroke-width="1" stroke-opacity=".7"/>`);
   tx(p,sec.title,122,hy,33,{fill:white,group:`header-${i}`});
   if(sec.limit)rich(p,[{text:'同一个三角洲UID',size:26,fill:white},{text:'仅可下一次',bold:true,size:26,fill:'#f8dda7',gap:6}],810,hy,26,`header-${i}`,{anchor:'end'});
   else tx(p,'机密 / 绝密',810,hy,26,{fill:'#d9e7ee',anchor:'end',bold:false,group:`header-${i}`});
   box(p,`header-${i}`,[44,hy-28,798,55],[63,hy-22,758,44]);
   sec.offers.forEach((o,j)=>{
    const group=`card-${i}-${j}`,cx=[215.5,504,732.5][j],color=o.mode==='机密'?mint:gold;
    tx(p,(o.superValue?'超值券 · ':'')+o.mode,cx,ty,j===0?30:31,{anchor:'middle',fill:color,group});
    num(p,o.price,'r',cx+(j===0?30:26),536+offset,j===0?46:40,ink,group+'-price');
    tx(p,'保底',cx,(j===0?570.5:590.6)+offset,j===0?22:24,{anchor:'middle',bold:false,fill:muted,group});
    num(p,o.guarantee,'w',cx+(j===0?51:30.5),(j===0?615:627)+offset,j===0?72:42,color,group+'-loot');
    const x=[44,400,622][j],w=[343,208,221][j];box(p,group,[x,458+offset,w,208],[x+23,476+offset,w-46,172]);
   });
  });
  rich(p,[{text:'03',bold:true,size:25,fill:'#f8dda7'},{text:'下单须知',bold:true,size:34,fill:white,gap:18}],443.5,1050,34,'rules-header',{anchor:'middle'});box(p,'rules-header',[44,1023,798,54],[65,1030,755,40]);
  tx(p,'地图说明',238,1134,34,{anchor:'middle',group:'map'});
  rich(p,[{text:'打手选图',bold:true},{text:'，下单可备注',gap:1}],89.75,1185.35,30,'map-intro');rich(p,[{text:'但',gap:0},{text:'需要排队。',bold:true,fill:gold,gap:1}],89.75,1222.35,30,'map-queue');
  raw(p,'<path d="M82 1247h312" stroke="#aec8d4" stroke-width="1.1"/><rect x="82.75" y="1260" width="70" height="38" rx="7" fill="#e3f2ed"/><rect x="82.75" y="1357" width="70" height="38" rx="7" fill="#f9ecd7"/>');
  tx(p,'机密',89.75,1279.35,29,{fill:mint,group:'map'});tx(p,c.rules.map.secret.slice(0,2).join(' / '),174.75,1279.35,28,{bold:false,group:'map'});tx(p,c.rules.map.secret[2],174.75,1319.35,28,{bold:false,group:'map'});
  tx(p,'绝密',89.75,1376.35,29,{fill:gold,group:'map'});tx(p,c.rules.map.absolute.join(' / '),174.75,1376.35,28,{bold:false,group:'map'});
  box(p,'map',[44,1095,390,321],[64,1110,348,289]);
  tx(p,'装备规范',647,1134,34,{anchor:'middle',group:'equipment'});
  const ruleX=488.1675,bodyX=577.1675,ruleSize=29;
  const ruleLabel=(label,cy,group,lineGroup)=>tx(p,label,ruleX,cy,ruleSize,{group,lineGroup,role:'rule-label'});
  const ruleBody=(runs,cy,group)=>rich(p,runs,bodyX,cy,ruleSize,group,{lineGroup:group});
  const ruleContinuation=(text,cy)=>tx(p,text,bodyX,cy,ruleSize,{bold:false,group:'equipment',role:'rule-body'});
  ruleLabel('房卡：',1178,'equipment-room-secret-label','equipment-room-secret');ruleBody([{text:'机密包',fill:mint},{text:'1张红卡房',bold:true,gap:1}],1178,'equipment-room-secret');
  ruleContinuation('绝密按俱乐部规定',1214);ruleContinuation('包金、红卡',1250);
  raw(p,'<path d="M488 1273h316 M488 1353h316" stroke="#aec8d4" stroke-width="1.1"/>');
  ruleLabel('背包：',1294,'equipment-bag-label','equipment-bag-secret');ruleBody([{text:'机密',fill:mint},{text:'≥'+c.rules.equipment.backpackMinimumSlots.secret+'格',bold:true,fill:mint,gap:1}],1294,'equipment-bag-secret');
  ruleBody([{text:'绝密',fill:gold},{text:'≥'+c.rules.equipment.backpackMinimumSlots.absolute+'格',bold:true,fill:gold,gap:1}],1330,'equipment-bag-absolute');
  ruleLabel('左轮：',1374,'equipment-revolver-label','equipment-revolver');ruleBody([{text:'均需携带'},{text:'≥'+c.rules.equipment.revolverMinimumCount+'把',bold:true,fill:gold,gap:1}],1374,'equipment-revolver');
  box(p,'equipment',[452,1095,390,321],[473,1110,348,289]);
  tx(p,c.rules.failedOrderCompensation.label,238,1470,30,{anchor:'middle',group:'compensation'});
  tx(p,'机密',151.75,1510,26,{anchor:'middle',fill:mint,group:'compensation'});tx(p,'绝密',325.25,1510,26,{anchor:'middle',fill:gold,group:'compensation'});
  raw(p,'<path d="M238.5 1497v69" stroke="#aec8d4" stroke-width="1.1"/>');
  num(p,'+'+c.rules.failedOrderCompensation.secret,'w',176.953,1548,45,mint,'compensation-secret');num(p,'+'+c.rules.failedOrderCompensation.absolute,'w',351.042,1548,45,gold,'compensation-absolute');
  box(p,'compensation',[44,1436,390,155],[65,1450,347,126]);
  tx(p,'活动限制',647,1470,30,{anchor:'middle',group:'restriction'});tx(p,'体验单',647,1516,28,{anchor:'middle',bold:false,group:'restriction'});tx(p,'不参与任何店铺活动',647,1553,30,{anchor:'middle',fill:'#9f4b43',group:'restriction'});
  box(p,'restriction',[452,1436,390,155],[473,1450,348,126]);
  tx(p,c.rules.footer,434,1689,22,{anchor:'middle',bold:false,group:'footer'});box(p,'footer',[210,1657,458,65],[219,1674,430,30]);
 }
 return {requests,pages};
}






