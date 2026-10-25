const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = vm.createContext({
  document: {addEventListener() {}}, console,
});
vm.runInContext(fs.readFileSync('app/static/js/monitor.js', 'utf8'), context);
for (const [iso, expected] of [
  ['2026-10-23T11:27:42+00:00', '14:27:42'],
  ['2026-10-25T11:27:42+00:00', '13:27:42'],
]) assert.equal(context.displayMonitorTime(iso), expected);
const source = fs.readFileSync('app/static/js/monitor.js', 'utf8');
assert.equal(source.includes('finished_at: new Date()'), false);
console.log('PASS: Kyiv completion times are independent of the browser timezone.');
