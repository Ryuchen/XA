// Actual React admin API + review persistence; HTTP-only contract.
import('./fronted-legacy-react-contracts.mjs').then(m => m.runScenario('admin-withdraw-lost-response-live')).catch(error => { console.error(error.stack || error); process.exitCode = 1; });
