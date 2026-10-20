// Regression: reconnects keep one heartbeat; overlapping refreshes share a request.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const intervals = new Map();
let nextTimer = 0, requests = 0, resolveRequest;
const context = vm.createContext({
  SESSION_ID: 1, IS_ACTIVE: true,
  document: {addEventListener() {}, getElementById() {return null;}},
  setInterval(fn) {intervals.set(++nextTimer, fn); return nextTimer;},
  clearInterval(id) {intervals.delete(id);}, clearTimeout() {}, setTimeout() {},
  fetch() {requests++; return new Promise(resolve => {resolveRequest = resolve;});},
  WebSocket: {OPEN: 1}, console,
});
vm.runInContext(fs.readFileSync('app/static/js/monitor.js', 'utf8'), context);
(async () => {
  for (let reconnect = 0; reconnect < 20; reconnect++) context.startPing();
  assert.equal(intervals.size, 1);
  const updates = Array.from({length: 50}, () => context.fetchSessionStatus());
  assert.equal(requests, 1);
  resolveRequest({ok: false});
  await Promise.all(updates);
  const next = context.fetchSessionStatus();
  assert.equal(requests, 2);
  resolveRequest({ok: false});
  await next;
  console.log('PASS: 20 reconnects retain one heartbeat; 50 overlapping refreshes use one request; later refresh still works.');
})().catch(error => {console.error(error); process.exitCode = 1;});
