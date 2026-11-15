// Current React controller contract; browser acceptance uses CUA.
import('./fronted-legacy-react-contracts.mjs').then(m => m.runScenario('three-role-application-progress-live')).catch(error => { console.error(error.stack || error); process.exitCode = 1; });
