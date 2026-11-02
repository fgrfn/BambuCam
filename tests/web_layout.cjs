// Guard the fluid console layout against fixed widths and horizontal overflow.
const assert = require('node:assert/strict');
const fs = require('node:fs');

const css = fs.readFileSync('bambucam/web/static/css/app.css', 'utf8');
const html = fs.readFileSync('bambucam/web/templates/index.html', 'utf8');

function rule(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return css.match(new RegExp('(?:^|\\n)' + escaped + '\\s*\\{([^}]+)\\}'))?.[1];
}

function mediaBlock(width) {
  return css.match(new RegExp('@media\\s*\\(max-width:\\s*' + width + 'px\\)\\s*\\{([\\s\\S]*?)\\n\\}'))?.[1];
}

// The page stays fluid so the live view can use the whole screen width.
const page = rule('.page');
assert(page, 'Page container must exist');
assert.match(page, /width:\s*100%/);
assert.match(page, /max-width:\s*none/);
assert.doesNotMatch(page, /max-width:\s*\d+px/);

// Live view and inspector: the video column shrinks, the inspector keeps its width.
const live = rule('.live');
assert(live, 'Live workspace grid must exist');
assert.match(live, /grid-template-columns:\s*minmax\(0,\s*1fr\)\s+\d+px/);
for (const selector of ['.stage', '.card', '.field']) {
  assert.match(rule(selector) || '', /min-width:\s*0/, selector + ' must be allowed to shrink without horizontal overflow');
}
assert.match(rule('.stream-view') || '', /aspect-ratio:\s*16\s*\/\s*9/);
assert.match(rule('.stream-view') || '', /max-width:\s*100%/);

// Breakpoints: inspector below the video on tablets, tabs on their own row on phones.
const tablet = mediaBlock(1100);
assert(tablet, 'Tablet breakpoint must exist');
assert.match(tablet, /\.live[^{]*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/);
const phone = mediaBlock(760);
assert(phone, 'Phone breakpoint must exist');
assert.match(phone, /\.tabs\s*\{[^}]*width:\s*100%/);

// Side gutters respect the phone's safe areas.
assert.match(page, /padding-left:\s*max\(var\(--gutter\),\s*env\(safe-area-inset-left/);
assert.match(page, /padding-right:\s*max\(var\(--gutter\),\s*env\(safe-area-inset-right/);
assert.match(html, /name="viewport"[^>]*viewport-fit=cover/);

console.log('Responsive console layout guard passed');
