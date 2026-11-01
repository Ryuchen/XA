// Pen advances control spacing; visible glyph bounds control centering.
export function inlineMetrics(parts,glyphs,gap=0) {
 const entries=[];let pen=0;
 for(const [i,p] of parts.entries()) {
  const g=glyphs[p.id],scale=p.size/100;
  if(i) {
   const prev=entries[i-1],explicit=p.gap??gap;
   const numericUnit=/^[rw]$/i.test(String(p.text))&&/[0-9]$/.test(String(prev.part.text));
   pen=numericUnit?prev.right+explicit-g.x*scale:pen+explicit;
  }
  const left=pen+g.x*scale,right=left+g.width*scale;
  entries.push({part:p,left,right,top:(g.y-g.ascent)*scale,bottom:(g.y+g.height-g.ascent)*scale});
  pen+=(g.advance??g.width)*scale;
 }
 const left=Math.min(...entries.map(e=>e.left)),right=Math.max(...entries.map(e=>e.right));
 const top=Math.min(...entries.map(e=>e.top)),bottom=Math.max(...entries.map(e=>e.bottom));
 return {entries,left,right,top,bottom,width:right-left,height:bottom-top};
}
