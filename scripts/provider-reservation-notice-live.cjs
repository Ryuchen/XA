// Current React controllers and real isolated HTTP; no legacy UI runtime.
import('./fronted-http-contracts.mjs').then(module => module.runScenario('provider-reservation-notice-live')).catch(error => { console.error(error.stack || error); process.exitCode = 1; });
