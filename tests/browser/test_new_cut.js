'use strict';
// Browser test: a cut that is new since the last visit opens automatically ("New cut … opened", "Back to …").
// Only against the synthetic test data (cuts cut4, cut5, cut6). BASE=http://127.0.0.1:8295/ PWFILE=... node test_new_cut.js
const { BASE, credentials, devices, launch, checks, main, sleep } = require('./common');

const { ok, done } = checks();
const errors = [];
async function loaded(p, cut) {
  const end = Date.now() + 20000;
  while (Date.now() < end) {
    const z = await p.evaluate(() => ({ sel: document.querySelector('#cut-select').value, src: document.querySelector('#video').getAttribute('src') || '' }));
    if (z.sel && z.src.includes(`media/${z.sel}/`) && (!cut || z.sel === cut)) return z.sel;
    await sleep(100);
  }
  return p.evaluate(() => document.querySelector('#cut-select').value);
}
const toast = (p) => p.evaluate(() => {
  const t = document.querySelector('#toast'), b = document.querySelector('#toast-button');
  return { visible: !t.hidden, text: document.querySelector('#toast-text').textContent, button: b.hidden ? '' : b.textContent };
});
async function toastShown(p, ms = 4000) {             // the message comes once the cut has finished loading
  const end = Date.now() + ms;
  while (Date.now() < end) { const t = await toast(p); if (t.visible) return t; await sleep(100); }
  return toast(p);
}
async function withStorage(ctx, values) {
  const p = await ctx.newPage();
  p.on('pageerror', (e) => errors.push(String(e)));
  await p.goto(BASE + 'api/cuts');                    // same origin, without starting the app
  await p.evaluate((w) => { localStorage.clear(); for (const [k, v] of Object.entries(w)) localStorage.setItem(k, v); }, values);
  return p;
}

main(async () => {
  const browser = await launch([]);
  const cuts = await (async () => {
    const c = await browser.newContext({ httpCredentials: credentials }); const q = await c.newPage();
    await q.goto(BASE + 'api/cuts'); const j = JSON.parse(await q.textContent('body')); await c.close(); return j.cuts.map((f) => f.id);
  })();
  ok('Test data: cut4, cut5, cut6', cuts.join(',') === 'cut4,cut5,cut6', cuts.join(','));

  // 1. old browser state: cut4 remembered, cut6 never seen -> cut6 opens, message with "Back to cut4"
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, httpCredentials: credentials, locale: 'en-US' });
  let p = await withStorage(ctx, { sf_name: 'Reviewer A', sf_cut: 'cut4' });
  await p.goto(BASE, { waitUntil: 'domcontentloaded' });
  let f = await loaded(p);
  let t = await toastShown(p);
  ok('The new cut opens instead of the remembered one', f === 'cut6', f);
  ok('Message "New cut cut6 opened" with "Back to cut4"', t.visible && t.text === 'New cut cut6 opened' && t.button === 'Back to cut4', JSON.stringify(t));
  ok('Address shows ?f=cut6', new URL(p.url()).searchParams.get('f') === 'cut6', p.url());
  ok('Seen cuts remembered', await p.evaluate(() => localStorage.getItem('sf_cuts_seen')) === 'cut4,cut5,cut6');
  await p.click('#toast-button');
  f = await loaded(p, 'cut4');
  ok('"Back to cut4" switches back', f === 'cut4' && new URL(p.url()).searchParams.get('f') === 'cut4', `${f} ${p.url()}`);
  await p.goto(BASE, { waitUntil: 'domcontentloaded' });
  f = await loaded(p); await sleep(2000); t = await toast(p);
  ok('Afterwards the chosen cut (cut4) stays, without a message', f === 'cut4' && !t.visible, `${f} ${JSON.stringify(t)}`);
  await p.close();

  // 2. the link names the cut explicitly -> no automatic switch
  p = await withStorage(ctx, { sf_name: 'Reviewer A', sf_cut: 'cut4' });
  await p.goto(BASE + '?f=cut5', { waitUntil: 'domcontentloaded' });
  f = await loaded(p); await sleep(2000); t = await toast(p);
  ok('A link with ?f=cut5 opens cut5, no message', f === 'cut5' && !t.visible, `${f} ${JSON.stringify(t)}`);
  await p.close();

  // 3. the very first visit -> newest cut, no message
  p = await withStorage(ctx, { sf_name: 'Reviewer A' });
  await p.goto(BASE, { waitUntil: 'domcontentloaded' });
  f = await loaded(p); await sleep(2000); t = await toast(p);
  ok('First visit: newest cut, no message', f === 'cut6' && !t.visible, `${f} ${JSON.stringify(t)}`);
  await p.close();

  // 4. the newest cut is the remembered one already -> nothing changes
  p = await withStorage(ctx, { sf_name: 'Reviewer A', sf_cut: 'cut6' });
  await p.goto(BASE, { waitUntil: 'domcontentloaded' });
  f = await loaded(p); await sleep(2000); t = await toast(p);
  ok('cut6 remembered: cut6, no message', f === 'cut6' && !t.visible, `${f} ${JSON.stringify(t)}`);
  await p.close();
  await ctx.close();

  // 5. iPhone (touch): cut5 remembered -> cut6, tapping "Back to cut5"
  const iphone = await browser.newContext({ ...devices['iPhone 13'], viewport: { width: 390, height: 844 }, httpCredentials: credentials, locale: 'en-US' });
  p = await withStorage(iphone, { sf_name: 'Reviewer B', sf_cut: 'cut5' });
  await p.goto(BASE, { waitUntil: 'domcontentloaded' });
  f = await loaded(p); t = await toastShown(p);
  ok('iPhone: new cut cut6 with "Back to cut5"', f === 'cut6' && t.visible && t.button === 'Back to cut5', `${f} ${JSON.stringify(t)}`);
  await p.tap('#toast-button');
  f = await loaded(p, 'cut5');
  ok('iPhone: a tap switches to cut5', f === 'cut5', f);
  await iphone.close();

  ok('No script errors', errors.length === 0, errors.join(' | '));
  await browser.close();
  done();
});
