// Current React controller contract; browser acceptance uses CUA.
import('./fronted-legacy-react-contracts.mjs').then(m => m.runScenario('provider-vue-report-label-375')).catch(error => { console.error(error.stack || error); process.exitCode = 1; });
