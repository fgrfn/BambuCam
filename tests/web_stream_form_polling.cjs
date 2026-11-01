// Regression test: live status polling must not destroy unsubmitted stream settings.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('bambucam/web/static/js/app.js', 'utf8');
const modesStart = source.indexOf('async function loadModelCapabilities');
const modesEnd = source.indexOf('// ── Stream status', modesStart);
const applyStart = source.indexOf('async function applyStreamSettings');
const applyEnd = source.indexOf('\n// ──', applyStart + 20);
assert(modesStart > 0 && modesEnd > modesStart && applyStart > 0 && applyEnd > applyStart);

function select() {
  return {
    value: '', options: [], rebuilds: 0,
    replaceChildren(...options) {
      this.options = options;
      this.value = String((options.find(option => option.selected) || options[0])?.value || '');
      this.rebuilds += 1;
    },
  };
}
const elements = {
  'sel-resolution': select(),
  'sel-framerate': select(),
  'sl-bitrate': { value: '4000', min: '100', max: '12000', disabled: false },
  'stream-img': { src: '/stream' },
};
const models = [
  { name: 'Camera', max_framerate: 63,
    supported_framerates: [60, 46, 30, 15, 10, 5],
    supported_resolutions: ['2592x1944', '1920x1080', '1296x972'],
    resolution_max_framerates: { '2592x1944': 15, '1920x1080': 32, '1296x972': 46 } },
  { name: 'Replacement', max_framerate: 30,
    supported_framerates: [30, 15], supported_resolutions: ['640x480'],
    resolution_max_framerates: { '640x480': 30 } },
];
let failApply = false;
let submitted = null;
const messages = [];
const context = vm.createContext({
  document: { getElementById: id => elements[id] },
  Option: function(label, value, _default, selected) {
    Object.assign(this, { label, value, selected });
  },
  api: async (method, path, payload) => {
    if (path === '/camera/models') return models;
    if (path === '/stream/settings') {
      if (failApply) throw Error('test apply failure');
      submitted = payload;
      return { restarted: true };
    }
    throw Error('Unexpected API call ' + path);
  },
  tr: (key, args) => args ? String(args.value) : key,
  btnLoading: () => {},
  toast: (message, type) => { messages.push([message, type]); },
  window: { dispatchEvent: () => {} },
  CustomEvent: class { constructor(type) { this.type = type; } },
});
vm.runInContext(
  'var resolutionMaxFps = {};\n' +
  'var streamEditorState = {dirty:false,applying:false,awaitingConfirmation:null,' +
  'modelKey:null,requestSequence:0,revision:0};\n' +
  source.slice(source.indexOf('function markStreamSettingsDirty()'),
    source.indexOf('// Bounds are supplied')) +
  source.slice(modesStart, modesEnd) + source.slice(applyStart, applyEnd),
  context
);
const res = elements['sel-resolution'];
const fps = elements['sel-framerate'];

const initial = {
  model: 'Camera', backend: 'picamera2', device: 'libcamera:0',
  resolution: '2592x1944', framerate: 15,
  output_modes: [
    { resolution: '2592x1944', kind: 'native', sensor_max_fps: 15.63 },
    { resolution: '1920x1080', kind: 'native', sensor_max_fps: 32.81 },
    { resolution: '1296x972', kind: 'native', sensor_max_fps: 46.34 },
  ],
  resolution_max_framerates: {
    '2592x1944': 15, '1920x1080': 32, '1296x972': 46,
  },
};

(async () => {
  await context.loadModelCapabilities(initial);
  assert.equal(res.value, '2592x1944');
  assert.equal(fps.value, '15');

  // Change the form but do not submit yet.
  res.value = '1920x1080';
  res.onchange();
  fps.value = '30';
  fps.onchange();
  assert.equal(context.streamEditorState.dirty, true);
  const rebuilds = res.rebuilds;

  // Repeated camera polling (and any asynchronous language-status refresh)
  // must not overwrite either draft or replace the focused select.
  for (let i = 0; i < 4; i += 1) {
    await context.loadModelCapabilities(initial);
    assert.equal(res.value, '1920x1080');
    assert.equal(fps.value, '30');
  }
  assert.equal(res.rebuilds, rebuilds);

  // Successful apply should preserve the selected values until server acknowledgement.
  await context.applyStreamSettings();
  assert.equal(submitted.resolution, '1920x1080');
  assert.equal(submitted.framerate, 30);
  assert.equal(submitted.bitrate_kbps, 4000);
  assert.equal(context.streamEditorState.awaitingConfirmation.resolution, '1920x1080');
  await context.loadModelCapabilities(initial);
  assert.equal(res.value, '1920x1080');

  await context.loadModelCapabilities({ ...initial, resolution: '1920x1080', framerate: 30 });
  assert.equal(context.streamEditorState.dirty, false);
  assert.equal(context.streamEditorState.awaitingConfirmation, null);

  // Without pending edits an external change should be reflected by polling.
  await context.loadModelCapabilities({ ...initial, resolution: '1296x972', framerate: 30 });
  assert.equal(res.value, '1296x972');

  // A failed apply never discards the draft.
  res.value = '1920x1080';
  res.onchange();
  failApply = true;
  await context.applyStreamSettings();
  await context.loadModelCapabilities({ ...initial, resolution: '1296x972', framerate: 30 });
  assert.equal(res.value, '1920x1080');
  assert.equal(context.streamEditorState.dirty, true);
  assert(messages.some(([, type]) => type === 'error'));

  // Switching to a different camera invalidates this camera's draft.
  await context.loadModelCapabilities({
    model: 'Replacement', backend: 'picamera2', device: 'libcamera:1',
    resolution: '640x480', framerate: 30, output_modes: [],
    resolution_max_framerates: { '640x480': 30 },
  });
  assert.equal(res.value, '640x480');
  assert.equal(context.streamEditorState.dirty, false);
  console.log('Stream settings polling regression passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
