// Current React presentation functions against real API data.
import('./fronted-http-contracts.mjs').then(module => module.runScenario('native-legacy-team-progress')).catch(error => { console.error(error.stack || error); process.exitCode = 1; });
