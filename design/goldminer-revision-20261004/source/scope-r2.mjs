export function validateDeliveryScope(state,c,plan,{final=false}={}){
const scope=state.deliveryScope,eq=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
if(!scope||!eq(scope.pageIds,['desktop'])||!eq(scope.platforms,['desktop']))throw Error('本轮仅修订附件端游海报');
if(!eq(c.scope.platforms,['desktop'])||c.platforms.length!==1||c.platforms[0].id!=='desktop'||c.tiers.length!==6)throw Error('本轮平台或六档结构不符');
if(c.scope.noConfidential!==true)throw Error('不得添加机密标签');
for(const t of c.tiers){
 if(!Number.isFinite(t.desktopPrice)||t.desktopPrice<=0||'mobilePrice' in t)throw Error('价目范围不符');
 if(t.processLabel!==(t.processValueCounts?'计入':'不计入')||!Number.isFinite(t.totalGoldRedSlots))throw Error('权益数据不一致');
}
const e=c.mechanics.example;
if(e.guaranteeAddedW!==e.goldSlots*c.mechanics.gold.perSlotGuaranteeW+e.redSlots*c.mechanics.red.perSlotGuaranteeW||e.remainingW!==e.guaranteeAddedW-e.extractedValueW)throw Error('示例算术冲突');
if(plan&&(!eq(plan.pages.map(p=>p.id),scope.pageIds)||!eq(plan.pages[0].platforms,['desktop'])))throw Error('布局与本轮范围不符');
if(final&&(!state.contentVerified||!state.contentVerificationBasis||!eq(state.verifiedPricePlatforms,['desktop'])||!state.priceSourceByPlatform.desktop||state.approvals.illustration.status!=='approved'||state.approvals.background.status!=='approved'))throw Error('来源或复用美术未核对');
return scope;
}

