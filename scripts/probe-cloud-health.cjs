/* Short HTTPS availability probe; no claim of business capacity. */
const { performance } = require('node:perf_hooks');
const fs = require('node:fs');
const path = require('node:path');
(async () => {
  const start=performance.now(), samples=[];
  while(performance.now()-start<18000) {
    try {
      const at=performance.now();
      const response=await fetch('https://api.xaesports.club/api/health/',{signal:AbortSignal.timeout(8000)});
      samples.push({ms:Math.round(performance.now()-at),ok:response.ok&&(await response.json()).status==='ok'});
    } catch {samples.push({ok:false});}
    await new Promise(resolve=>setTimeout(resolve,250));
  }
  const values=samples.filter(row=>row.ok).map(row=>row.ms).sort((a,b)=>a-b);
  const report={requests:samples.length,failures:samples.filter(row=>!row.ok).length,
    p95_ms:values[Math.ceil(values.length*.95)-1],duration_ms:Math.round(performance.now()-start),
    scenario:'one web replica stopped; public HTTPS health checks; not a capacity test'};
  fs.writeFileSync(path.resolve(__dirname,'../.tools/tencent-deploy/cloud-failover.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
  if(report.failures)process.exitCode=1;
})().catch(error=>{console.error(error.message);process.exitCode=1;});
