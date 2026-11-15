// Actual React withdrawal presenters/navigation and real scoped HTTP.
import('../fronted-legacy-react-contracts.mjs').then(m => m.runScenario('provider-vue-withdraw-notice-375')).catch(error => { console.error(error.stack || error); process.exitCode = 1; });
