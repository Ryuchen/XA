"use strict";
const pages = window.SONIC_PAGES;
const pageById = new Map(pages.map(page => [page.id, page]));
const primary = ["home", "companions", "orders", "messages", "mine"];
const main = document.querySelector("main");
const routeCount = new Set(pages.map(p => p.route.split("?")[0]).filter(v => v !== "*")).size;
const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[ch]);
const img = p => "screens/" + encodeURIComponent(p.id) + ".png";
const go = (id,label) => '<a href="#p/'+encodeURIComponent(id)+'">'+esc(label)+'</a>';
const template = (p, featured) => '<a href="#p/'+p.id+'" class="'+(featured?'cover':'card')+'"><img '+(featured?'':'loading="lazy" ')+'src="thumbnails/'+p.id+'.webp" alt="'+esc(p.title)+'设计稿"><b>'+esc(p.title)+'</b>'+(featured?'':'<small>'+esc(p.area)+'</small>')+'</a>';
let query = "", area = "";
function drawGrid(){
  const filtered = pages.filter(p => (!area || p.area === area) && [p.title,p.id,p.route,...p.sections.map(s=>s.name)].join(" ").toLowerCase().includes(query.toLowerCase().trim()));
  document.querySelector(".counter").textContent = filtered.length+" 张";
  document.querySelector(".grid").innerHTML = filtered.length ? filtered.map(p=>template(p,false)).join("") : '<p class="empty">没有匹配的设计稿。</p>';
}
function directory(){
  document.title="声浪 · 老板端移动设计稿";
  const uniqueAreas=[...new Set(pages.map(p=>p.area))];
  main.innerHTML='<div class="intro"><div><h1>声浪 · 母带</h1><p>老板端移动 H5 设计稿。图中账户、人物、金额和记录均为评审样例。</p></div><div class="fig">'+routeCount+' 条业务路由　/　'+pages.length+' 张原稿</div></div><div class="featured">'+primary.map(id=>template(pageById.get(id),true)).join("")+'</div><div class="panelhead"><h2>按流程查看</h2></div><div class="journeys">'+[["catalog","选服务与下单"],["reservations","预约与转单"],["order-detail","履约与售后"],["wallet","钱包与流水"],["points","积分与兑奖"],["messages","消息与客服"],["login","进入本店"]].map(([id,label])=>go(id,label+' →')).join("")+'</div><section id="all"><div class="panelhead"><h2>全部页面</h2></div><div class="controls"><label class="sr" for="search">搜索设计稿</label><input id="search" type="search" placeholder="搜索页面、路由或功能"><label class="sr" for="area">业务范围</label><select id="area"><option value="">全部业务范围</option>'+uniqueAreas.map(a=>'<option>'+esc(a)+'</option>').join("")+'</select><span class="counter" aria-live="polite"></span></div><div class="grid"></div></section><footer>声浪电竞 · 移动端视觉设计预览</footer>';
  const search=document.getElementById("search"), choice=document.getElementById("area");
  search.value=query;choice.value=area;
  search.oninput=()=>{query=search.value;drawGrid()};choice.onchange=()=>{area=choice.value;drawGrid()};drawGrid();
}
function detail(p){
  document.title=p.title+" · 声浪移动设计稿";
  const pos=pages.indexOf(p), links=p.actions.filter(a=>pageById.has(a.to));
  main.innerHTML='<a href="#all" class="back">← 返回目录</a><article class="detail"><figure class="art"><a href="'+img(p)+'" target="_blank" rel="noopener"><img src="'+img(p)+'" alt="'+esc(p.title)+'移动端完整原稿"></a><figcaption>点按原图可放大。账户与交易数据为设计示例。</figcaption></figure><div class="review"><div class="meta"><span>'+esc(p.area)+'</span><code>'+esc(p.route)+'</code></div><div class="heading"><h1>'+esc(p.title)+'</h1></div><p class="lead">'+esc(p.layout)+'</p><a class="original" href="'+img(p)+'" target="_blank" rel="noopener">打开 PNG 原图 ↗</a><h2>从页首到页尾</h2><ol class="blocks">'+p.sections.map(s=>'<li><strong>'+esc(s.name)+'</strong><p>'+esc(s.body)+'</p></li>').join("")+'</ol>'+(links.length?'<h2>关联页面与操作稿</h2><nav class="links" aria-label="关联页面">'+links.map(a=>go(a.to,a.label)).join("")+'</nav>':'')+'<h2>页面状态</h2><ul class="states">'+p.states.map(s=>'<li>'+esc(s)+'</li>').join("")+'</ul><nav class="prevnext" aria-label="翻页">'+(pos?go(pages[pos-1].id,"← 上一张"):"<span></span>")+(pos<pages.length-1?go(pages[pos+1].id,"下一张 →"):"")+'</nav></div></article>';
}
function render(){
  const id=location.hash.startsWith("#p/") ? location.hash.slice(3) : "";
  const p=pageById.get(id);
  if(p){detail(p);window.scrollTo(0,0)}else{directory();if(location.hash==="#all")document.getElementById("all").scrollIntoView();else window.scrollTo(0,0)}
}
window.addEventListener("hashchange",render);render();

