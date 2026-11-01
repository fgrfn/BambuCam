// Exercise the shipped bitrate control against server-provided limits.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('bambucam/web/static/js/app.js', 'utf8');
const elements = {
  'sel-resolution': {value: '2592x1944'},
  'sl-bitrate': {value: 20000, disabled: true},
  'val-bitrate': {}, 'hint-bitrate': {},
};
const context = vm.createContext({
  document: {getElementById: id => elements[id]},
  setText: (id, value) => {elements[id].textContent = value;},
  tr: (key, args) => JSON.stringify(args),
});
const start = source.indexOf('function updateBitrateLimits()');
const end = source.indexOf("document.getElementById('sel-resolution')?.addEventListener", start);
vm.runInContext('let bitratePolicy = null; let bitrateLimitsByResolution = {};\n' +
  source.slice(start, end), context);
context.updateBitrateLimits();
assert(elements['sl-bitrate'].disabled);
vm.runInContext(`bitratePolicy = {min_kbps:100, max_kbps:12000, resolution:'1920x1080'};
  bitrateLimitsByResolution = {'640x480':{min_kbps:100,max_kbps:2000,resolution:'640x480'}};`, context);
context.updateBitrateLimits();
assert.equal(elements['sl-bitrate'].max, 12000);
assert.equal(elements['sl-bitrate'].value, 12000);
assert.equal(elements['sl-bitrate'].disabled, false);
assert.match(elements['hint-bitrate'].textContent, /1920x1080/);
elements['sel-resolution'].value = '640x480';
context.updateBitrateLimits();
assert.equal(elements['sl-bitrate'].max, 2000);
assert.equal(elements['sl-bitrate'].value, 2000);
assert.equal(elements['val-bitrate'].textContent, 2000);
elements['sl-bitrate'].value = 50;
context.updateBitrateLimits();
assert.equal(elements['sl-bitrate'].value, 100);
console.log('Bitrate UI behavior passed');
