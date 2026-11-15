// Current React controllers; real HTTP fixture transport.
import('./fronted-http-contracts.mjs').then(module => module.runScenario('native-chat-send-recovery-live')).catch(error => { console.error(error.stack || error); process.exitCode = 1; });
