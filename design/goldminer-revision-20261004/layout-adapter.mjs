import fs from 'node:fs';
const zones=JSON.parse(fs.readFileSync(new URL('./source/background-regions-stage2.json',import.meta.url),'utf8'));
export function makeLayout(c){
 const requests=[],pages=[],memo=new Map();let p;
 const F={body:'Microsoft YaHei UI',num:'Bahnschrift',art:'Noto Sans SC Black'};
 const C={ink:'#213F53',muted:'#526C79',ivory:'#FFFCF4',gold:'#9F6828',red:'#AC4C47',green:'#52765F',line:'#B8CED4'};
 const request=(text,font,bold=false)=>{text=String(text);const key=JSON.stringify([text,font,bold]);if(!memo.has(key)){const id='t'+requests.length;memo.set(key,id);requests.push({id,text,font,style:bold?1:0});}return memo.get(key);};
 function text(text,x,cy,size,opt={}){
  text=String(text);const {font=F.body,bold=false,anchor='middle',fill=C.ink,role='copy',group=role+'-'+cy,maxWidth}=opt;
  p.elements.push({type:'text',id:request(text,font,bold),text,x,cy,size,anchor,fill,role,group,lineGroup:opt.lineGroup||group,maxWidth});
 }
 function rich(runs,x,cy,size,opt={}){
  const {anchor='middle',fill=C.ink,role='copy',group=role+'-'+cy,maxWidth}=opt;
  p.elements.push({type:'rich',runs:runs.map(r=>({...r,text:String(r.text),id:request(r.text,r.font||F.body,r.bold===true),size:r.size||size,fill:r.fill||fill})),x,cy,size,anchor,role,group,maxWidth});
 }
 function art(text,x,cy,size,width,group='title'){
  p.elements.push({type:'art',ids:[...text].map(t=>request(t,F.art)),text,x,cy,size,maxWidth:width,anchor:'middle',fill:C.ink,shadow:'#A2BBC9',outline:C.ivory,outlineWidth:2.2,angles:true,group});
 }
 const raw=(markup,group)=>p.elements.push({type:'raw',markup,group});
 function money(amount,right,rx,cy,size,group){
  text(amount,right,cy,size,{anchor:'end',font:F.num,bold:true,fill:C.gold,role:'price-number',group});
  text('r',rx,cy,size*.96,{anchor:'start',font:F.num,bold:true,fill:C.gold,role:'price-unit',group});
 }
 function slots(n,cx,cy,group,size=44){rich([{text:n,font:F.num,bold:true,size},{text:'格',size:size*.72,gap:7}],cx,cy,size,{group,role:'slot-count'});}
 function newPage(id,label,bg,platforms,width,height,ew=3200){
  p={id,label,bg,platforms,w:width,h:height,ew,eh:Math.round(ew*height/width),elements:[],auditGroups:[]};pages.push(p);
 }
 const audit=(name,prefix,rect,options={})=>p.auditGroups.push({name,prefix,rect,...options});
 for(const pf of c.platforms){
  const g=zones.poster;
  newPage(pf.id,c.brand+' · '+c.title+' · '+pf.name+'价目','poster-r1.png',[pf.id],836,1880);
  text(c.brand,203,88,32,{bold:true,group:'hero-copy-brand',role:'brand'});
  art(c.title,203,162,82,299,'hero-copy-title');
  text(c.subtitle,203,234,25,{group:'hero-copy-subtitle',role:'tagline'});
  text(pf.name+' · 六档挖矿套餐',205.5,317.5,26,{bold:true,group:'platform',role:'platform'});
  ['版本','价格','金红总格数','带出过程'].forEach((h,i)=>text(h,[110,281,485.5,702][i],385,31,{bold:true,fill:C.ivory,group:'table-header-'+i,role:'table-heading'}));
  c.tiers.forEach((t,i)=>{
   const cy=g.tableRows[i].cy;
   text(t.name,110,cy,36,{bold:true,group:'tier-'+i+'-name',role:'tier-name'});
   money(t[pf.priceField],318,325,cy,52,'tier-'+i+'-price');
   slots(t.totalGoldRedSlots,485.5,cy,'tier-'+i+'-slots',46);
   text(t.processLabel,702,cy,34,{bold:!t.processValueCounts,fill:t.processValueCounts?C.green:C.ink,group:'tier-'+i+'-process',role:'process'});
   const edges=g.tableColumnEdges;
   for(const [j,suffix]of ['name','price','slots','process'].entries())audit('第'+(i+1)+'档'+suffix,'tier-'+i+'-'+suffix,[edges[j]+12,cy-29,edges[j+1]-edges[j]-24,58],{centerY:true,centerX:suffix!=='price',toleranceX:suffix==='price'?15:2});
  });
  text(c.copy.mechanicsHeading,418,955,33,{bold:true,fill:C.ivory,group:'rules-heading',role:'section-heading'});
  for(const [kind,x]of [['red',232.5],['gold',630]]){
   const data=c.mechanics[kind],group='benefit-'+kind;
   text('摸到1格'+(kind==='red'?'红':'金'),x,1029,31,{bold:true,group:group+'-label',role:'benefit-label'});
   rich([{text:'基础保底 ',size:29},{text:'+'+data.perSlotGuaranteeW,font:F.num,bold:true,size:40,fill:kind==='red'?C.red:C.gold},{text:'w',font:F.num,bold:true,size:38.4,fill:kind==='red'?C.red:C.gold,gap:5}],x,1071,29,{group:group+'-value',role:'benefit-value'});
   audit(group,group,g[kind+'Benefit'].safe,{centerX:true,centerY:true});
  }
  text('格数满后，保底不再叠加',418.5,1137.5,30,{bold:true,group:'cap-rule',role:'cap-rule'});
  const e=c.mechanics.example;
  text(e.label,419.5,1203.398926,28,{bold:true,fill:C.green,group:'example-heading',role:'example-heading'});
  rich([{text:e.goldSlots+'格金×'+c.mechanics.gold.perSlotGuaranteeW+'w＋'+e.redSlots+'格红×'+c.mechanics.red.perSlotGuaranteeW+'w＝'},{text:e.guaranteeAddedW+'w',bold:true}],419.5,1238.398926,30,{group:'example-formula',role:'example'});
  rich([{text:'本局收获'+e.extractedValueW+'w，打手还差'},{text:e.remainingW+'w',bold:true,fill:C.red}],419.5,1274.398926,30,{group:'example-result',role:'example'});
  text(c.copy.noticesHeading,418,1339.5,27.5,{bold:true,fill:C.ivory,group:'notice-heading',role:'section-heading'});
  const accumulation=c.notices.find(n=>n.id==='accumulation-excludes-destruction-loss');
  const [before,after]=accumulation.shortText.split(accumulation.emphasis);
  const rules=[
   [[{text:'标准版、进阶版、终极版：',bold:true}],[{text:'带出过程金额'},{text:'不计入总保底',bold:true},{text:'。'}],[{text:before},{text:accumulation.emphasis,bold:true},{text:after}]],
   [[{text:'仅计算'},{text:'撤离成功',bold:true},{text:'，失败不计算。'}]],
   [[{text:'按撤离成功后老板的'},{text:'行动收获截图',bold:true},{text:'计算；'}],[{text:'吃不下的金和红，打手帮带也计入。'}]],
   [[{text:'打手负责理包，优先整理'},{text:'高价值物品',bold:true},{text:'。'}]],
   [[{text:'最后一把金红超过格数时，'},{text:'优先计算红',bold:true},{text:'。'}]]
  ];
  const ys=[[1425.5,1461.5,1497.5],[1537.5],[1577.5,1613.5],[1653.5],[1693.5]];
  rules.forEach((lines,i)=>{
   const cy=(ys[i][0]+ys[i].at(-1))/2;
   raw('<circle cx="132.465597" cy="'+cy+'" r="13" fill="#638276"/>','notice-number-'+i);
   text(i+1,132.465597,cy,20,{font:F.num,bold:true,fill:C.ivory,group:'notices-number-'+i,role:'notice-number'});
   lines.forEach((runs,j)=>rich(runs,157.465597,ys[i][j],31.5,{anchor:'start',group:'notices-'+i+'-'+j,role:'rule-body'}));
  });
  audit('标题组','hero-copy',g.title,{centerX:true,centerY:true});
  audit('平台条','platform',g.platform,{centerX:true,centerY:true});
  audit('表头','table-header',g.tableHeader,{centerY:true});
  audit('玩法规则标题','rules-heading',g.rulesHeading,{centerY:true});
  audit('格满停止','cap-rule',g.capRibbon,{centerX:true,centerY:true});
  audit('计入档示例','example',g.example.safe,{centerX:true,centerY:true});
  audit('须知标题','notice-heading',g.noticesHeading,{centerY:true});
  audit('五项须知','notices-',g.notices.safe,{centerX:true,centerY:true});
  p.audit={ordinaryBodySize:31.5,phoneBodySize:31.5*390/836,ruleSourceIds:c.notices.map(n=>n.id),prices:c.tiers.map(t=>t[pf.priceField]),slots:c.tiers.map(t=>t.totalGoldRedSlots)};
 }
 const s=zones.social;
 newPage('social',c.brand+' · '+c.title+' · 双端价格封面','social-r2.png',['desktop','mobile'],1086,1448,3000);
 text(c.brand,264,74.14795,37,{bold:true,group:'hero-copy-brand',role:'brand'});
 art(c.title,264,160.14795,110,411,'hero-copy-title');
 text(c.subtitle,264,255.14795,32,{group:'hero-copy-subtitle',role:'tagline'});
 text(c.copy.socialHeading,252,339.5,30,{bold:true,group:'platform',role:'platform'});
 ['版本','金红格数','带出过程','端游价格','手游价格'].forEach((h,i)=>text(h,[135,296,456.5,666,914][i],459,31.5,{bold:true,fill:C.ivory,group:'table-header-'+i,role:'table-heading'}));
 c.tiers.forEach((t,i)=>{
  const cy=s.tableRows[i].cy;
  text(t.name,135,cy,40,{bold:true,group:'tier-'+i+'-name',role:'tier-name'});
  slots(t.totalGoldRedSlots,296,cy,'tier-'+i+'-slots',45);
  text(t.processLabel,456.5,cy,39,{bold:!t.processValueCounts,fill:t.processValueCounts?C.green:C.ink,group:'tier-'+i+'-process',role:'process'});
  money(t.desktopPrice,711,719,cy,63,'tier-'+i+'-desktop');
  money(t.mobilePrice,959,967,cy,63,'tier-'+i+'-mobile');
  const edges=s.tableColumnEdges;
  for(const [j,suffix]of ['name','slots','process','desktop','mobile'].entries())audit('对照第'+(i+1)+'档'+suffix,'tier-'+i+'-'+suffix,[edges[j]+10,cy-36,edges[j+1]-edges[j]-20,72],{centerY:true,centerX:!['desktop','mobile'].includes(suffix),toleranceX:18});
 });
 rich([{text:'红每格 +'+c.mechanics.red.perSlotGuaranteeW+'w',bold:true,fill:C.red},{text:'   ·   '},{text:'金每格 +'+c.mechanics.gold.perSlotGuaranteeW+'w',bold:true,fill:C.gold}],543,1262.5,39,{group:'feature',role:'selling-point'});
 audit('广告标题','hero-copy',s.title,{centerX:true,centerY:true});
 audit('广告平台条','platform',s.platform,{centerX:true,centerY:true});
 audit('对照表头','table-header',s.tableHeader,{centerY:true});
 audit('核心保底','feature',s.feature,{centerX:true,centerY:true});
 p.audit={noNotices:true,sourceTierIds:c.tiers.map(t=>t.id)};
 return {schemaVersion:3,revision:'r4',requests,pages};
}
