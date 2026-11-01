import {createHash} from 'node:crypto';
export function makeLayout(c){
 if(c.tiers.length!==4||c.tierNames.length!==5)throw new Error('本轮须为四版各三地图五档');
 for(const t of c.tiers){if(t.maps.length!==3||t.targets.length!==5)throw new Error('地图/目标不完整');for(const m of t.maps)if(m.prices.length!==5||m.guarantees.length!==5||[...m.prices,...m.guarantees].some(n=>!Number.isFinite(n)||n<0))throw new Error('价目不完整');}
 const CN='Microsoft YaHei UI',NUM='Bahnschrift',ART='Noto Sans SC Black';
 const ink='#23465b',white='#fffdf7',secondary='#506d80';
 const palette={gold:'#a56524',teal:'#197d78'};
 const requests=[],ids=new Map();
 const request=(text,font=CN,style=0)=>{const key=JSON.stringify([String(text),font,style]);if(!ids.has(key)){const id='t'+createHash('sha256').update(key).digest('hex').slice(0,20);ids.set(key,id);requests.push({id,text:String(text),font,style});}return ids.get(key);};
 function page(id,label,bg,w,h,ew,eh,platforms){return{id,label,bg,w,h,ew,eh,platforms,elements:[],auditGroups:[],businessBindings:[]};}
 function tx(p,text,x,cy,size,{font=CN,bold=false,fill=ink,anchor='start',maxWidth,role='copy',lineGroup,group,stroke,strokeWidth}={}){p.elements.push({type:'text',id:request(text,font,bold?1:0),text:String(text),x,cy,size,fill,anchor,maxWidth,role,lineGroup,group,stroke,strokeWidth});}
 function rich(p,runs,x,cy,size,{fill=ink,anchor='start',maxWidth,role='rule-body',group}={}){p.elements.push({type:'rich',x,cy,size,anchor,maxWidth,role,group,runs:runs.map(r=>{if(typeof r==='string')r={text:r};const font=r.font||CN,bold=r.bold||false;return{...r,id:request(r.text,font,bold?1:0),size:r.size||size,fill:r.fill||fill,gap:r.gap??0};})});}
 function art(p,text,x,cy,size,{maxWidth,outlineWidth=2.5,fill=ink,angles=true}={}){p.elements.push({type:'art',text,ids:[...text].map(ch=>request(ch,ART,0)),x,cy,size,anchor:'middle',maxWidth,fill,outline:white,outlineWidth,shadow:'#9ebfcf',angles});}
 function audit(p,id,roles,safe,{centerX,centerY,toleranceX=2,toleranceY=2,kind='copy',indices,groupName,groupPrefix}={}){p.auditGroups.push({id,roles,safe,centerX,centerY,toleranceX,toleranceY,kind,indices,groupName,groupPrefix});}
 function amount(p,value,unit,unitX,cy,size,color,group,role){
  tx(p,value,unitX-7,cy,size,{font:NUM,bold:true,fill:color,anchor:'end',role:role+'-number',lineGroup:group,group});
  tx(p,unit,unitX,cy,size,{font:NUM,bold:true,fill:color,role:role+'-unit',lineGroup:group,group});
 }
 function numRuns(value,unit,size,color=ink){return[{text:String(value),font:NUM,size,bold:true,fill:color},{text:unit,font:NUM,size,bold:true,fill:color,gap:3.5}];}
 function ruleRuns(tokens,color,size=26){return tokens.map(t=>typeof t==='string'?t:{...t,fill:t.bold?color:ink,size:t.size||size});}
 function full(t){
  const color=palette[t.color],h=t.color==='gold'?2171:2172;
  const p=page(t.id,`环太平洋 · ${t.platform}${t.mode}价目`,`poster-${t.color}-r2.png`,724,h,3200,Math.round(h*3200/724),[t.platform==='端游'?'desktop':'mobile']);
  tx(p,c.brand,191.5,93.5,23,{bold:true,anchor:'middle',role:'brand'});
  art(p,c.title,175,141.5,55,{maxWidth:235});
  tx(p,`${t.platform} · ${t.mode}版`,176.5,240.5,25,{bold:true,fill:white,anchor:'middle',maxWidth:224,role:'mode'});
  tx(p,c.subtitle,363,309,30,{bold:true,anchor:'middle',maxWidth:614,role:'objective'});
  audit(p,'brand',['brand'],[84,80,299,107],{centerX:191.5,centerY:93.5});
  audit(p,'title',['art-title'],[51,111,299,172],{centerX:175,centerY:141.5});
  audit(p,'mode',['mode'],[62,226,291,255],{centerX:176.5,centerY:240.5});
  audit(p,'objective',['objective'],[52,292,674,326],{centerX:363,centerY:309});
  const maps=[{title:352,header:[412,447],rows:[448,652]},{title:666,header:[734,769],rows:[770,976]},{title:990,header:[1064,1099],rows:[1100,1306]}];
  const columns=[[52,218],[233,357],[370,506],[522,674]];
  t.maps.forEach((m,i)=>{
   const b=maps[i],role=`map-${i}-title`;
   tx(p,m.name,427.5,(b.title+15+b.header[0]-10)/2,32,{bold:true,anchor:'middle',role});
   audit(p,role,[role],[185,b.title+15,670,b.header[0]-10],{centerX:427.5,centerY:(b.title+15+b.header[0]-10)/2});
   c.copy.columns.forEach((label,j)=>{
    const r=`map-${i}-head-${j}`,x=(columns[j][0]+columns[j][1])/2,cy=(b.header[0]+6+b.header[1]-5)/2;
    tx(p,label,x,cy,23,{bold:true,fill:white,anchor:'middle',role:r,lineGroup:`head-${i}`});
    audit(p,r,[r],[columns[j][0],b.header[0]+6,columns[j][1],b.header[1]-5],{centerX:x,centerY:cy});
   });
   for(let j=0;j<5;j++){
    const rowH=(b.rows[1]-b.rows[0])/5,cy=b.rows[0]+(j+.5)*rowH,group=`row-${i}-${j}`,prefix=group+'-';
    tx(p,c.tierNames[j],135,cy,26,{bold:j===4,anchor:'middle',maxWidth:162,role:prefix+'tier',lineGroup:group,group});
    amount(p,m.prices[j],'r',336,cy,36,ink,group,prefix+'price');
    const target=t.targets[j].replace('不同','');
    tx(p,target,438,cy,26,{anchor:'middle',maxWidth:132,role:prefix+'target',lineGroup:group,group});
    amount(p,m.guarantees[j],'w',635,cy,36,color,group,prefix+'loot');
    const safeY=[b.rows[0]+j*rowH+4,b.rows[0]+(j+1)*rowH-4];
    audit(p,prefix+'tier',[prefix+'tier'],[52,safeY[0],218,safeY[1]],{centerX:135,centerY:cy,toleranceY:3});
    audit(p,prefix+'price',[prefix+'price-number',prefix+'price-unit'],[233,safeY[0],357,safeY[1]],{centerY:cy,toleranceY:3,kind:'price'});
    audit(p,prefix+'target',[prefix+'target'],[370,safeY[0],506,safeY[1]],{centerX:438,centerY:cy,toleranceY:3});
    audit(p,prefix+'loot',[prefix+'loot-number',prefix+'loot-unit'],[522,safeY[0],674,safeY[1]],{centerY:cy,toleranceY:3,kind:'loot'});
    p.businessBindings.push({type:'map-tier',map:i,tier:j,price:m.prices[j],guarantee:m.guarantees[j],canonicalTarget:t.targets[j],displayTarget:target,roles:{price:prefix+'price-number',guarantee:prefix+'loot-number',target:prefix+'target',tier:prefix+'tier'}});
   }
  });
  tx(p,c.copy.addonsTitle,397.5,1320.5,20,{bold:true,anchor:'middle',role:'addons-title'});
  audit(p,'addons-title',['addons-title'],[180,1310,615,1331],{centerX:397.5,centerY:1320.5});
  tx(p,c.copy.prophetTitle,142,1372.5,24,{bold:true,anchor:'middle',role:'prophet-title'});
  rich(p,[{text:'每局 ',size:23},...numRuns('+'+t.rules.prophetFee,'r',30,color)],574,1372.5,23,{anchor:'middle',maxWidth:158,group:'prophet-fee',role:'fee'});
  tx(p,c.copy.prophetIntro,363.5,1410,26,{anchor:'middle',maxWidth:615,role:'prophet-intro'});
  for(const [j,cx]of [200.5,526.5].entries()){
   tx(p,c.copy.prophetConditions[j],cx,1467,26,{bold:true,fill:color,anchor:'middle',maxWidth:292,role:`prophet-${j}-condition`});
   rich(p,[...numRuns('+'+t.rules.prophetRewards[j],'w',34,color),{text:c.copy.baseGuarantee,size:26,gap:8}],cx,1502,26,{anchor:'middle',maxWidth:292,group:`prophet-${j}-reward`,role:'reward'});
   audit(p,`prophet-${j}`,['prophet-'+j+'-condition'],[j?379:53,1450,j?674:348,1518],{centerX:cx,centerY:1484,toleranceY:3,kind:'compound',groupName:`prophet-${j}-reward`});
  }
  audit(p,'prophet-title',['prophet-title'],[60,1360,224,1385],{centerX:142,centerY:1372.5});
  audit(p,'prophet-fee',[],[494,1360,654,1385],{centerX:574,centerY:1372.5,indices:[],kind:'rich-group',groupName:'prophet-fee'});
  audit(p,'prophet-intro',['prophet-intro'],[53,1396,674,1424],{centerX:363.5,centerY:1410});
  tx(p,c.copy.doorTitle,142,1586.5,24,{bold:true,anchor:'middle',role:'door-title'});
  const activation=[...ruleRuns([{text:c.copy.doorPrefix},{text:String(c.commonRules.doorItemCount)+'个'+t.rules.doorItem,bold:true},{text:'（'}],color),...numRuns(t.rules.doorThreshold,'w',30,color),{text:'以上',bold:true,fill:color},{text:'）生效。'}];
  const door=[activation,...c.copy.doorLines.map(line=>ruleRuns(line,color))];
  door.forEach((runs,j)=>rich(p,runs,53,1638+j*38,26,{maxWidth:621,group:`door-${j}`,role:'door-body'}));
  audit(p,'door-title',['door-title'],[60,1574,224,1599],{centerX:142,centerY:1586.5});
  audit(p,'door-body',[],[53,1618,674,1772],{centerY:1695,toleranceY:3,kind:'rich-family',groupPrefix:'door-'});
  tx(p,c.copy.noticeTitle,147.5,1840.5,24,{bold:true,anchor:'middle',role:'notice-title'});
  const min=t.rules.minimum,third=t.maps[2].name;
  const notice1=[{text:'只计成功撤离。巴克什/航天基地低于'},...numRuns(min['巴克什'],'w',30,color),{text:'、'}];
  const notice2=[{text:third+'低于'},...numRuns(min[third],'w',30,color),{text:t.rules.forbidPrisonHelicopter?'算流局；':'不计入，算作流局。'},...(t.rules.forbidPrisonHelicopter?[{text:'监狱禁走直升机撤离点。',bold:true,fill:color}]:[])];
  const notice3=[{text:'在已成功解锁的复活点复活后，撤离失败'},{text:c.commonRules.failureComparison+c.commonRules.failureLimit+'次',bold:true,fill:color},{text:'，'}];
  const notice4=[{text:c.copy.failureEffect}];
  [notice1,notice2,notice3,notice4].forEach((runs,j)=>rich(p,runs,53,[1888,1922,1960,1994][j],26,{maxWidth:621,role:'notice-body',group:`notice-${j}`}));
  audit(p,'notice-title',['notice-title'],[61,1828,234,1853],{centerX:147.5,centerY:1840.5});
  audit(p,'notice-body',[],[53,1870,674,2012],{centerY:1941,toleranceY:3,kind:'rich-family',groupPrefix:'notice-'});
  tx(p,c.brand+' · '+c.copy.unitLegend,407,2059.5,21,{anchor:'middle',maxWidth:417,role:'footer',stroke:white,strokeWidth:1.4});
  audit(p,'footer',['footer'],[197,2042,617,2077],{centerX:407,centerY:2059.5});
  p.readability={nativeRuleFont:26,phoneEquivalent:26*390/724};
  return p;
 }
 function social(){
  const p=page('social','环太平洋 · 四版体验价广告','social-r2.png',1086,1448,3000,4000,['desktop','mobile']);
  tx(p,c.brand,282.5,124.5,37,{bold:true,anchor:'middle',role:'brand'});
  art(p,c.title,282,200.5,65,{maxWidth:399,outlineWidth:3.8});
  tx(p,c.subtitle,545,415,44,{bold:true,anchor:'middle',maxWidth:914,role:'objective'});
  audit(p,'brand',['brand'],[85,105,480,144],{centerX:282.5,centerY:124.5});audit(p,'title',['art-title'],[78,164,486,237],{centerX:282,centerY:200.5});audit(p,'objective',['objective'],[82,381,1008,449],{centerX:545,centerY:415});
  [{x:47,y:503,id:'desktop-absolute'},{x:560,y:503,id:'desktop-secret'},{x:47,y:807,id:'mobile-absolute'},{x:560,y:807,id:'mobile-secret'}].forEach((card,i)=>{
   const t=c.tiers.find(t=>t.id===card.id),color=palette[t.color],startPrice=t.maps[0].prices[0],guarantee=t.maps[0].guarantees[0];
   if(!t.maps.every(m=>m.prices[0]===startPrice&&m.guarantees[0]===guarantee))throw new Error('广告体验价不统一，须重排对应地图');
   tx(p,`${t.platform} · ${t.mode}`,card.x+238,card.y+31,34,{bold:true,anchor:'middle',fill:white,role:`card-${i}-mode`});
   rich(p,[...numRuns(startPrice,'r',89,color),{text:'起',size:37,gap:13}],card.x+241,card.y+146,40,{anchor:'middle',maxWidth:381,group:`ad-price-${i}`,role:'ad-price'});
   rich(p,[{text:c.copy.baseGuarantee,size:32,gap:0},...numRuns(guarantee,'w',39,color).map((r,j)=>({...r,gap:j?4:11}))],card.x+239.5,card.y+243.5,32,{anchor:'middle',maxWidth:406,group:`ad-loot-${i}`,role:'ad-loot'});
   audit(p,`ad-mode-${i}`,['card-'+i+'-mode'],[card.x+21,card.y+14,card.x+455,card.y+48],{centerX:card.x+238,centerY:card.y+31});
   audit(p,`ad-price-${i}`,[],[card.x+45,card.y+104,card.x+437,card.y+188],{centerX:card.x+241,centerY:card.y+146,kind:'rich-group',groupName:`ad-price-${i}`});
   audit(p,`ad-loot-${i}`,[],[card.x+31,card.y+227,card.x+448,card.y+260],{centerX:card.x+239.5,centerY:card.y+243.5,kind:'rich-group',groupName:`ad-loot-${i}`});
   p.businessBindings.push({type:'ad-start',variant:t.id,price:startPrice,guarantee,target:t.targets[0],priceGroup:`ad-price-${i}`,lootGroup:`ad-loot-${i}`});
  });
  tx(p,c.copy.adExperience,542.5,1135,42,{bold:true,anchor:'middle',role:'ad-condition'});
  tx(p,c.copy.adScope,542.5,1177,40,{anchor:'middle',role:'ad-scope'});
  audit(p,'ad-bottom',['ad-condition','ad-scope'],[75,1113,1010,1198],{centerX:542.5,centerY:1155.5,toleranceY:3});
  return p;
 }
 function cover(){
  const p=page('cover','环太平洋 · 端手游横版封面','cover-r1.png',1671,941,3200,1802,['desktop','mobile']);
  tx(p,c.brand,428.5,188,61,{bold:true,anchor:'middle',role:'brand'});
  art(p,c.title,428.5,309,110,{maxWidth:622,outlineWidth:5.5});
  c.copy.coverBenefitLines.forEach((line,i)=>tx(p,line,428.5,[416,475][i],60,{bold:i===1,anchor:'middle',maxWidth:604,role:'cover-subtitle'}));
  tx(p,c.copy.coverPlatforms,428.5,597,50,{bold:true,fill:white,anchor:'middle',role:'cover-platforms'});
  tx(p,c.copy.coverModes,428.5,698.5,61,{bold:true,anchor:'middle',role:'cover-modes'});
  audit(p,'brand',['brand'],[151,151,706,221],{centerX:428.5,centerY:188});
  audit(p,'title',['art-title'],[106,246,751,372],{centerX:428.5,centerY:309});
  audit(p,'subtitle',['cover-subtitle'],[125,386,733,507],{centerX:428.5,centerY:445,toleranceY:3});
  audit(p,'platforms',['cover-platforms'],[116,572,741,622],{centerX:428.5,centerY:597});
  audit(p,'modes',['cover-modes'],[116,661,741,736],{centerX:428.5,centerY:698.5});
  return p;
 }
 return{requests,pages:[...c.tiers.map(full),social(),cover()]};
}
