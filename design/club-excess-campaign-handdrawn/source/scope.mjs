const defaultScope={
  pageIds:['desktop-absolute','desktop-secret','social','cover'],
  platforms:['desktop'],
  basis:'未确认手游时默认端游范围',
};
const platformName={desktop:'端游',mobile:'手游'};

export function validateDeliveryScope(state,content,plan,{final=false}={}){
  const scope=state.deliveryScope??defaultScope;
  const pages=scope.pageIds,platforms=scope.platforms;
  if(!Array.isArray(pages)||!pages.length||new Set(pages).size!==pages.length||pages.some(id=>typeof id!=='string'||!id)){
    throw new Error('campaign-state.json 的 deliveryScope.pageIds 必须列出不重复的本轮页面 ID。');
  }
  if(!Array.isArray(platforms)||!platforms.length||new Set(platforms).size!==platforms.length||platforms.some(p=>!platformName[p])){
    throw new Error('deliveryScope.platforms 只能从 desktop / mobile 中选择。');
  }
  for(const id of pages){
    if(id.startsWith('desktop-')&&!platforms.includes('desktop'))throw new Error('端游页面不在 deliveryScope.platforms 中: '+id);
    if(id.startsWith('mobile-')&&!platforms.includes('mobile'))throw new Error('手游页面不在 deliveryScope.platforms 中: '+id);
  }
  const tiers=content?.tiers;
  if(Array.isArray(tiers)){
    for(const tier of tiers){
      const platform=tier.platform==='端游'?'desktop':tier.platform==='手游'?'mobile':null;
      if(platform&&!platforms.includes(platform))throw new Error(`content.json 含本轮未确认的${tier.platform}价目 ${tier.id}；先核实是否制作手游，并按交付范围移除历史样例档位。`);
    }
    if(pages.some(id=>id==='social'||id.startsWith('desktop-'))&&platforms.includes('desktop')&&!tiers.some(t=>t.platform==='端游'))throw new Error('本轮需要端游价目，但 content.json 没有端游档位。');
    if(pages.some(id=>id==='social'||id.startsWith('mobile-'))&&platforms.includes('mobile')&&!tiers.some(t=>t.platform==='手游'))throw new Error('本轮需要手游价目，但 content.json 没有手游档位。');
    for(const id of pages.filter(id=>id.startsWith('desktop-')||id.startsWith('mobile-'))){
      const tier=tiers.find(t=>t.id===id);
      const expected=id.startsWith('desktop-')?'端游':'手游';
      if(!tier||tier.platform!==expected)throw new Error(`页面 ${id} 缺少匹配的${expected}价目数据。`);
    }
  }
  if(plan){
    const actual=plan.pages?.map(p=>p.id)??[];
    if(actual.length!==pages.length||actual.some(id=>!pages.includes(id)))throw new Error(`布局页面与本轮交付范围不一致：应为 ${pages.join(', ')}；当前为 ${actual.join(', ')}。修改项目适配器、广告/封面内容与底图后重排，不能悄悄删去页面或留下手游空卡。`);
    for(const page of plan.pages.filter(p=>p.id==='social'||p.id==='cover')){
      if(!Array.isArray(page.platforms)||page.platforms.length!==platforms.length||page.platforms.some(p=>!platforms.includes(p)))throw new Error(`${page.id} 的 platforms 标记与本轮范围不符；重排广告/封面并核对平台文案。`);
    }
  }
  if(final){
    if(typeof state.contentVerificationBasis!=='string'||!state.contentVerificationBasis.trim())throw new Error('请记录 contentVerificationBasis，说明本轮业务数据的核对依据。');
    if(typeof scope.basis!=='string'||!scope.basis.trim())throw new Error('请记录 deliveryScope.basis，说明客户点名页面或默认范围的依据。');
    const verified=state.verifiedPricePlatforms??[];
    for(const platform of platforms){
      if(pages.some(id=>id==='social'||id.startsWith(platform+'-'))){
        if(!verified.includes(platform)||typeof state.priceSourceByPlatform?.[platform]!=='string'||!state.priceSourceByPlatform[platform].trim())throw new Error(`${platformName[platform]}价目尚未记录已核对来源；请更新 verifiedPricePlatforms 与 priceSourceByPlatform，不能用历史样例价格导出。`);
      }
    }
  }
  return scope;
}
