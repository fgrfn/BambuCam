// Guard against regressions to the fixed-width desktop layout.
const assert = require('node:assert/strict');
const fs = require('node:fs');

const css = fs.readFileSync('bambucam/web/static/css/app.css', 'utf8');
const main = css.match(/(?:^|\n)\s*main\s*\{([^}]+)\}/)?.[1];
assert(main, 'Main grid must exist');
assert.match(main, /grid-template-columns:\s*clamp\([^;]+minmax\(0,\s*1fr\)[^;]*clamp\(/);
assert.match(main, /max-width:\s*none/);
assert.match(main, /width:\s*100%/);
assert.doesNotMatch(main, /max-width:\s*\d+px/);

for (const selector of ['info-panel', 'stream-panel', 'sidebar']) {
  assert.match(css, new RegExp('\\.' + selector + '\\s*\\{[^}]*min-width:\\s*0', 's'),
    selector + ' must be allowed to shrink without horizontal overflow');
}
assert.match(css, /@media\s*\(max-width:\s*1180px\)\s*\{/);
assert.match(css, /grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/);
assert.match(css, /@media\s*\(max-width:\s*720px\)\s*\{/);
assert.match(css, /grid-template-columns:\s*minmax\(0,\s*1fr\)/);
assert.match(css, /\.stream-view\s*\{[^}]*aspect-ratio:\s*16\s*\/\s*9/s);

console.log('Responsive stream layout guard passed');
