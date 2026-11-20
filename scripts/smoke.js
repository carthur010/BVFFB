// Safety check: load the site with the current data files and render every page.
// Fails (exit 1) if any page throws or shows a broken value, so a bad refresh never goes live.
// Usage: npm install --no-save jsdom && node scripts/smoke.js
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const file = n => { const p = path.join(root, 'data', n); return fs.existsSync(p) ? fs.readFileSync(p, 'utf8') : null; };

const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'https://bvffb.test/#home', pretendToBeVisual: true });
const w = dom.window;
w.scrollTo = () => {};
w.matchMedia = w.matchMedia || (() => ({ matches: false, addListener() {}, removeListener() {} }));
w.fetch = u => {
  const body = file(String(u).split('?')[0].split('/').pop());
  return Promise.resolve(body == null ? { ok: false, status: 404, headers: { get: () => null } }
    : { ok: true, headers: { get: () => null }, json: () => Promise.resolve(JSON.parse(body)) });
};
const errors = [];
w.console.error = (...a) => errors.push(a.map(x => (x && x.stack) || String(x)).join(' '));
w.addEventListener('error', e => errors.push(String(e.message)));
for (const s of w.document.querySelectorAll('script')) {
  try { w.eval(s.textContent); } catch (e) { errors.push(e.stack); }
}

const wait = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  await wait(2000);
  const data = JSON.parse(file('league.json'));
  const years = Object.keys(data.S);
  const routes = ['home', 'now', 'standings', 'champions', 'ratings', 'trends', 'timeline', 'h2h', 'achievements',
    'records-games', 'records-seasons', 'records-playoffs', 'records-streaks', 'records-career', 'records-players',
    'draft', 'draft-all', 'trades', 'waivers', 'franchise', 'versus-0-1',
    ...years.map(y => 'season-' + y), ...years.map(y => 'draft-' + y),
    ...data.M.map((_, i) => 'franchise-' + i)];
  // this week's matchup previews and last week's game recaps
  w.location.hash = 'now'; await wait(150);
  w.document.querySelectorAll('a[href^="#matchup-"],a[href^="#game-"]').forEach(a => routes.push(a.getAttribute('href').slice(1)));
  const bad = [];
  for (const r of [...new Set(routes)]) {
    w.location.hash = r;
    await wait(80);
    const t = w.document.getElementById('app').textContent;
    const hits = ['undefined', 'NaN', '[object Object]', 'Something went wrong'].filter(k => t.includes(k));
    if (t.trim().length < 50) hits.push('empty page');
    if (hits.length) bad.push(`${r}: ${hits.join(', ')}`);
  }
  if (errors.length) bad.push(...errors.slice(0, 5).map(e => 'error: ' + e.split('\n')[0]));
  if (bad.length) { console.log('Safety check FAILED:\n  ' + bad.join('\n  ')); process.exit(1); }
  console.log(`Safety check passed: ${new Set(routes).size} pages rendered cleanly.`);
  process.exit(0);
})();
