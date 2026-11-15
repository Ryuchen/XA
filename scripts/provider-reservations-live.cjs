// Current React controllers; real HTTP fixture transport.
import('./fronted-http-contracts.mjs').then(module => module.runScenario('provider-reservations-live')).catch(error => { console.error(error.stack || error); process.exitCode = 1; });
