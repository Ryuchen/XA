import {createHash} from 'node:crypto';
export function makeLayout(c){
 const ink='#203f53',muted='#557184',mint='#187b75',gold='#a96725',white='#fffdf6',CN='Microsoft YaHei UI',NUM='Bahnschrift',ART='Noto Sans SC Black';
 const requests=[],keys=new Map(),pages=[];
 const req=(text,font=CN,style=1)=>{text=String(text);const key=JSON.stringify([text,font,style]);if(!keys.has(key)){const id='t'+createHash('sha256').update(key).digest('hex').slice(0,20);keys.set(key,id);requests.push({id,text,font,style});}return keys.get(key);};
 const page=(id,label,bg,w,h,ew,eh,platforms)=>{let p={id,label,bg,w,h,ew,eh,platforms,elements:[],containers:[]};pages.push(p);return p;};
 const tx=(p,text,x,cy,size=30,opt={})=>{const {font=CN,bold=true,fill=ink,anchor='start',...rest}=opt;p.elements.push({type:'text',text:String(text),id:req(text,font,bold?1:0),x,cy,size,fill,anchor,...rest});};
 const art=(p,text,x,cy,size,maxWidth,group)=>p.elements.push({type:'art',text,ids:[...text].map(t=>req(t,ART,0)),x,cy,size,maxWidth,fill:ink,shadow:'#afcadb',outline:white,outlineWidth:2.6,angles:true,group});
 const raw=(p,markup)=>p.elements.push({type:'raw',markup});
 const rich=(p,runs,x,cy,size,group,opt={})=>p.elements.push({type:'rich',runs:runs.map(r=>({text:r.text,id:req(r.text,r.font||CN,r.bold?1:0),fill:r.fill||ink,size:r.size||size,bold:!!r.bold,gap:r.gap})),x,cy,size,fill:ink,group,role:'rule-body',...opt});
 const box=(p,id,bounds,safe,centered=true)=>p.containers.push({id,bounds,safe,centered});
 function brand(p,x,cy,size){raw(p,`<path d="M${x} ${cy+size*.43}l${size*.47} ${-size*.9}l${size*.48} ${size*.9}z M${x+size*.27} ${cy+size*.27}l${size*.20} ${-size*.39}l${size*.21} ${size*.39}z" fill="none" stroke="${ink}" stroke-width="${size*.075}" stroke-linejoin="round"/>`);tx(p,c.brand,x+size*1.22,cy,size,{role:'brand',group:'brand'});}
 function num(p,value,unit,ux,cy,size,color,group){tx(p,value,ux-6,cy,size,{font:NUM,fill:color,anchor:'end',group,lineGroup:group,role:unit==='r'?'price-number':'loot-number'});tx(p,unit,ux,cy,size*.94,{font:NUM,fill:color,group,lineGroup:group,role:unit==='r'?'price-unit':'loot-unit'});}
 const cover=page('cover','兴安电竞 · 体验单封面','experience-cover.png',1672,941,3200,1801,['desktop','mobile']);
 brand(cover,86,155,45);art(cover,'超值',82,307,142,450,'headline');art(cover,'体验单',82,492,163,494,'headline');
 tx(cover,'新客专享 · 每日一单',368,739,43,{fill:white,anchor:'middle',group:'claim'});
 tx(cover,'端游 / 手游 · 机密 / 绝密',368,822,31,{anchor:'middle',group:'platform'});
 box(cover,'brand',[70,115,530,90],[80,125,485,70]);box(cover,'headline',[60,207,545,400],[80,215,515,380]);box(cover,'claim',[70,693,599,91],[90,707,555,63]);box(cover,'platform',[80,790,560,56],[100,802,535,35]);
 const social=page('social','兴安电竞 · 体验单广告页','experience-social.png',1086,1448,2400,3200,['desktop','mobile']);
 brand(social,83,126,29);art(social,'超值',82,204,69,310,'headline');art(social,'体验单',82,284,91,340,'headline');
 tx(social,'新客专享：'+c.tiers[0].sections[0].limit,543,442,31,{anchor:'middle',group:'condition'});
 box(social,'brand',[65,90,405,75],[75,105,385,55]);box(social,'headline',[60,159,410,174],[75,165,385,170]);box(social,'condition',[30,416,1025,52],[80,425,925,35]);
 for(let i=0;i<4;i++){
  const tier=c.tiers[i%2],section=tier.sections[i<2?0:1],dx=i%2?512:0,headerY=i<2?523:920,ys=i<2?[608,703,797]:[1005,1099,1190];
  tx(social,tier.platform+' · '+section.title,288+dx,headerY,32,{fill:white,anchor:'middle',group:`header-${i}`});
  box(social,`header-${i}`,[54+dx,headerY-34,470,68],[72+dx,headerY-25,434,50]);
  section.offers.forEach((o,j)=>{
   const y=ys[j],color=o.mode==='机密'?mint:gold,group=`offer-${i}-${j}`;
   if(o.superValue){tx(social,o.mode,90+dx,y-15,25,{fill:color,group});tx(social,'超值券',90+dx,y+17,23,{fill:gold,group});}else tx(social,o.mode,90+dx,y,29,{fill:color,group});
   num(social,o.price,'r',262+dx,y,38,ink,group+'-price');tx(social,'保',310+dx,y,24,{bold:false,group,lineGroup:group+'-loot'});num(social,o.guarantee,'w',456+dx,y,50,color,group+'-loot');
   box(social,group,[62+dx,y-42,452,84],[80+dx,y-33,417,66]);
  });
 }
 for(const tier of c.tiers){
  const p=page(tier.id,'兴安电竞 · '+tier.platform+'体验单','experience-price.png',887,1774,2400,4800,[tier.platform==='端游'?'desktop':'mobile']);
  brand(p,65,88,27);art(p,'体验单',63,167,79,280,'headline');tx(p,c.game+' · '+tier.platform,214,229,27,{anchor:'middle',group:'subtitle'});
  box(p,'brand',[52,60,325,50],[62,69,305,39]);box(p,'headline',[50,118,330,96],[60,120,312,96]);box(p,'subtitle',[60,208,315,39],[65,214,305,32]);
  tier.sections.forEach((sec,i)=>{
   const hy=i===0?414:730,ty=i===0?494:810,offset=i*316;
   tx(p,sec.title,68,hy,32,{fill:white,group:`header-${i}`});
   if(sec.limit)tx(p,sec.limit,810,hy,23.5,{fill:white,anchor:'end',bold:false,group:`header-${i}`});
   box(p,`header-${i}`,[44,hy-28,798,55],[63,hy-22,758,44]);
   sec.offers.forEach((o,j)=>{
    const group=`card-${i}-${j}`,cx=[215,503,733][j],color=o.mode==='机密'?mint:gold;
    tx(p,(o.superValue?'超值券 · ':'')+o.mode,cx,ty,j===0?30:31,{anchor:'middle',fill:color,group});
    num(p,o.price,'r',cx+18,536+offset,j===0?46:40,ink,group+'-price');
    tx(p,'保底',cx,569+offset,21,{anchor:'middle',bold:false,fill:muted,group});
    num(p,o.guarantee,'w',cx+(j===0?45:30),615+offset,j===0?72:46,color,group+'-loot');
    const x=[44,400,622][j],w=[343,208,221][j];box(p,group,[x,458+offset,w,208],[x+23,476+offset,w-46,176]);
   });
  });
  tx(p,'下单须知',443.5,1050,34,{fill:white,anchor:'middle',group:'rules-header'});box(p,'rules-header',[44,1023,798,54],[65,1030,755,40]);
  tx(p,'地图说明',238,1134,32,{anchor:'middle',group:'map'});
  tx(p,'打手选图，下单可备注',67,1180,30,{bold:false,group:'map'});tx(p,'但需要排队。',67,1217,30,{bold:false,group:'map'});
  tx(p,'机密',67,1274,29,{fill:mint,group:'map'});tx(p,c.rules.map.secret.slice(0,2).join(' / '),152,1274,28,{bold:false,group:'map'});tx(p,c.rules.map.secret[2],152,1314,28,{bold:false,group:'map'});
  tx(p,'绝密',67,1371,29,{fill:gold,group:'map'});tx(p,c.rules.map.absolute.join(' / '),152,1371,28,{bold:false,group:'map'});
  box(p,'map',[44,1095,390,321],[64,1110,348,289]);
  tx(p,'装备规范',647,1134,32,{anchor:'middle',group:'equipment'});
  const ruleX=475,ruleSize=29;
  rich(p,[{text:'房卡：',bold:true},{text:'机密包1张红卡房'}],ruleX,1178,ruleSize,'equipment-room-secret');
  tx(p,'绝密按俱乐部规定',ruleX,1211,ruleSize,{bold:false,group:'equipment'});tx(p,'包金、红卡',ruleX,1244,ruleSize,{bold:false,group:'equipment'});
  rich(p,[{text:'背包：',bold:true},{text:'机密≥'+c.rules.equipment.backpackMinimumSlots.secret+'格'}],ruleX,1283,ruleSize,'equipment-bag-secret');
  tx(p,'绝密≥'+c.rules.equipment.backpackMinimumSlots.absolute+'格',ruleX,1316,ruleSize,{bold:false,group:'equipment'});
  rich(p,[{text:'左轮：',bold:true},{text:'两种模式均需'}],ruleX,1355,ruleSize,'equipment-revolver');tx(p,'携带至少'+c.rules.equipment.revolverMinimumCount+'把',ruleX,1388,ruleSize,{bold:false,group:'equipment'});
  box(p,'equipment',[452,1095,390,321],[473,1110,348,295]);
  tx(p,c.rules.failedOrderCompensation.label,238,1470,30,{anchor:'middle',group:'compensation'});
  tx(p,'机密',111,1510,26,{fill:mint,group:'compensation'});tx(p,'绝密',286,1510,26,{fill:gold,group:'compensation'});
  num(p,'+'+c.rules.failedOrderCompensation.secret,'w',183,1548,45,mint,'compensation-secret');num(p,'+'+c.rules.failedOrderCompensation.absolute,'w',356,1548,45,gold,'compensation-absolute');
  box(p,'compensation',[44,1436,390,155],[65,1450,347,126]);
  tx(p,'活动限制',647,1470,30,{anchor:'middle',group:'restriction'});tx(p,'体验单不参与',647,1518,30,{anchor:'middle',bold:false,group:'restriction'});tx(p,'任何店铺活动',647,1555,30,{anchor:'middle',group:'restriction'});
  box(p,'restriction',[452,1436,390,155],[473,1450,348,126]);
  tx(p,c.rules.footer,443.5,1688,22,{anchor:'middle',bold:false,group:'footer'});box(p,'footer',[210,1657,458,65],[219,1674,430,30]);
 }
 return {requests,pages};
}

