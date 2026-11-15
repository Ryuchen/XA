// Current React coupon presentation, hub and real isolated HTTP/WebSocket.
import('../fronted-legacy-react-contracts.mjs').then(m => m.runScenario('native-coupon-stock-realtime')).catch(error => { console.error(error.stack || error); process.exitCode = 1; });
