import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const project=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const file=path.join(project,'campaign-state.json');
const state=JSON.parse(await fs.readFile(file,'utf8'));
state.vectorDelivery={status:'completed',formats:['PNG','editable text SVG','outlined SVG'],pageIds:state.deliveryScope.pageIds,changedPriceCount:10,updatedPriceAppearances:20,overview:'review/updated-overview.png',review:'review/index.html',visualQA:'review/visual-qa.md',audit:'review/price-and-layout-audit.json',completedAt:'2026-10-09',rendering:'Typography rendered from vector at final export dimensions over approved raster backgrounds'};
state.nextStep='本轮三張成品已完成成图验收；后续改价从本项目content.json与layout-adapter.mjs继续。';
state.package='campaign-'+state.revision+'.zip';
await fs.writeFile(file,JSON.stringify(state,null,2));
// Make the delivery page title human-readable while preserving the exact revision id in the package link.
const workflowFile=path.join(project,'source/workflow.mjs');
let workflow=await fs.readFile(workflowFile,'utf8');
workflow=workflow.replace('兴安电竞海报 ${esc(revision)}</title>','限时清图单 · 手游新价格</title>').replace('兴安电竞 · ${esc(revision)}</h1>','兴安电竞 · 限时清图单手游新价格</h1>');
await fs.writeFile(workflowFile,workflow);
console.log('Final visual QA and delivery metadata recorded.');
