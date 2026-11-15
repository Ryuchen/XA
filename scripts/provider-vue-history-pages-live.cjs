// Current React controller contract; browser acceptance uses CUA.
import('./fronted-legacy-react-contracts.mjs').then(m => m.runScenario('provider-vue-history-pages-live')).catch(error => { console.error(error.stack || error); process.exitCode = 1; });
