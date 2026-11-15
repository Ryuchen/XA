// Production React controllers + real isolated HTTP/Redis; DOM is accepted via CUA.
import('../fronted-final-four-contracts.mjs').then(async m => {
  const { readInput } = await import('../fronted-test-runtime.mjs');
  await m.reportReview(await readInput());
}).catch(error => { console.error(error.stack || error); process.exitCode = 1; });
