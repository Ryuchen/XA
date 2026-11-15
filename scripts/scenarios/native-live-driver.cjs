// Current React controllers and real isolated HTTP; no legacy UI runtime.
import('../fronted-http-contracts.mjs').then(module => module.runScenario('native-live-driver')).catch(error => { console.error(error.stack || error); process.exitCode = 1; });
