export function validateDeliveryScope(state,c,plan,{final=false}={}){
const scope=state.deliveryScope,eq=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
if(!scope||!eq(scope.pageIds,['desktop','mobile','social'])||!eq(scope.platforms,['desktop','mobile']))throw Error('本轮为两端价目和附图双端价格封面');
if(!eq(c.scope.platforms,scope.platforms)||!eq(c.platforms.map(p=>p.id),scope.platforms)||c.tiers.length!==6)throw Error('本轮平台或六档结构不符');
if(c.scope.noConfidential!==true)throw Error('不得添加机密标签');
for(const pf of c.platforms)for(const t of c.tiers){
 if(!Number.isFinite(t[pf.priceField])||t[pf.priceField]<=0)throw Error('平台价目缺失');
 if(t.processLabel!==(t.processValueCounts?'计入':'不计入')||!Number.isFinite(t.totalGoldRedSlots))throw Error('权益数据不一致');
}
const e=c.mechanics.example;
if(e.guaranteeAddedW!==e.goldSlots*c.mechanics.gold.perSlotGuaranteeW+e.redSlots*c.mechanics.red.perSlotGuaranteeW||e.remainingW!==e.guaranteeAddedW-e.extractedValueW)throw Error('示例算术冲突');
if(plan){
 if(!eq(plan.pages.map(p=>p.id),scope.pageIds))throw Error('布局页集合与范围不符');
 for(const p of plan.pages)if(!eq(p.platforms,p.id==='social'?['desktop','mobile']:[p.id]))throw Error('单页平台不符');
}
if(final&&(!state.contentVerified||!state.contentVerificationBasis||!eq(state.verifiedPricePlatforms,scope.platforms)||!scope.platforms.every(p=>state.priceSourceByPlatform[p])||state.approvals.illustration.status!=='approved'||state.approvals.background.status!=='approved'))throw Error('来源或复用美术未核对');
return scope;
}
