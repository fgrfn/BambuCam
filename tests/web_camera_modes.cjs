// Exercise the actual dropdown builder without a browser or camera.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('bambucam/web/static/js/app.js', 'utf8');
const start = source.indexOf('async function loadModelCapabilities');
const end = source.indexOf('// ── Stream status', start);
const elements = Object.fromEntries(['sel-resolution', 'sel-framerate'].map(id => [id, {
  value: '', options: [],
  replaceChildren(...options) {
    this.options = options;
    this.value = String((options.find(o => o.selected) || options[0]).value);
  },
}]));
const context = vm.createContext({
  document: { getElementById: id => elements[id] },
  Option: function(label, value, _default, selected) { Object.assign(this, {label, value, selected}); },
  api: async () => [{ name: 'Camera', max_framerate: 90, supported_framerates: [90,60,30,15,5],
    supported_resolutions: ['640x480'], resolution_max_framerates: {} }],
  tr: (key, args) => args ? String(args.value) : key,
});
vm.runInContext('var resolutionMaxFps = {};\n' +
  'var streamEditorState = {dirty:false,applying:false,awaitingConfirmation:null,' +
  'modelKey:null,requestSequence:0,revision:0};\n' +
  source.slice(source.indexOf('function markStreamSettingsDirty()'),
    source.indexOf('// Bounds are supplied')) + source.slice(start, end), context);
(async () => {
  const status = { model: 'Camera', resolution: '2592x1944', framerate: 12,
    output_modes: [
      {resolution: '2592x1944', kind: 'native', sensor_max_fps: 15.63, source: 'detected'},
      {resolution: '1280x720', kind: 'scaled', sensor_resolution: '1296x972',
       sensor_max_fps: 46.34, source: 'model_fallback'},
    ],
    resolution_max_framerates: {'2592x1944': 15, '1280x720': 46},
  };
  await context.loadModelCapabilities(status);
  assert.equal(elements['sel-resolution'].options.length, 2);
  assert.match(elements['sel-resolution'].options[0].label, /^2592×1944 \(15.63\)$/);
  assert.match(elements['sel-resolution'].options[1].label, /scaled.*1296x972.*46.34.*fallback/);
  assert.equal(elements['sel-framerate'].value, '12');
  assert(elements['sel-framerate'].options.every(o => o.value <= 15));
  elements['sel-resolution'].value = '1280x720';
  elements['sel-resolution'].onchange();
  assert(elements['sel-framerate'].options.some(o => o.value === 46));
  assert(elements['sel-framerate'].options.every(o => o.value <= 46));

  // Camera state can still expose full-resolution still modes, while the
  // stream editor must show only the documented video-safe resolutions.
  context.streamEditorState.dirty = false;
  await context.loadModelCapabilities({
    ...status,
    video_output_modes: [
      {resolution: '1280x720', kind: 'scaled', sensor_resolution: '1296x972',
       sensor_max_fps: 46.34, max_fps: 46, aspect: '16:9', fov_width_pct: 100},
      {resolution: '1920x1080', kind: 'native', sensor_max_fps: 32.81, max_fps: 30,
       aspect: '16:9', fov_width_pct: 74},
    ],
    video_resolution_max_framerates: {'1920x1080': 30, '1280x720': 46},
  });
  assert.equal(elements['sel-resolution'].options.length, 2);
  assert.equal(elements['sel-resolution'].value, '1920x1080');
  assert(!elements['sel-resolution'].options.some(o => o.value === '2592x1944'));
  // Largest first, and a centre-cropped native mode is labelled as such.
  assert.equal(elements['sel-resolution'].options[0].value, '1920x1080');
  assert.equal(elements['sel-resolution'].options[0].label, '1920×1080 (16:9, 74, 30)');
  assert.equal(elements['sel-resolution'].options[1].label,
    '1280×720 (16:9, camera.mode.fullWidth, 1296x972, 46)');
  assert(!elements['sel-framerate'].options.some(o => o.value > 30));

  console.log('Camera UI behavior passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
