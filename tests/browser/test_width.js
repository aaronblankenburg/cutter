'use strict';
// Browser test: page width on a phone (390 px, iPhone). For every cut nothing may stick out on the right and the page
// must not scroll sideways (otherwise iOS zooms out and everything gets small). Works in any UI language.
// BASE=http://127.0.0.1:8295/ PWFILE=... [SHOTS=dir] node test_width.js
const { BASE, credentials, PHONE, launch, checks, main, sleep, watch, screenshot } = require('./common');

const { ok, done } = checks();
const WIDTH = PHONE.viewport.width;

main(async () => {
  const browser = await launch([]);
  const errors = [];
  const cuts = await (async () => {
    const c = await browser.newContext({ httpCredentials: credentials }); const q = await c.newPage();
    await q.goto(BASE + 'api/cuts'); const j = JSON.parse(await q.textContent('body')); await c.close(); return j.cuts.map((f) => f.id);
  })();
  const ctx = await browser.newContext({ ...PHONE, httpCredentials: credentials });
  const p = await ctx.newPage(); watch(p, errors);
  await p.addInitScript(() => localStorage.setItem('sf_name', 'Reviewer Phone'));
  for (const cut of cuts) {
    await p.goto(`${BASE}?f=${cut}`, { waitUntil: 'domcontentloaded' });
    for (let i = 0; i < 100 && !(await p.evaluate(() => document.querySelector('#video').readyState >= 1)); i++) await sleep(200);
    await sleep(800);
    // measured against the width of the phone: if something is wider, the layout viewport (innerWidth) grows with it
    const r = await p.evaluate((W) => {
      const out = [];
      for (const e of document.querySelectorAll('body *')) {
        const q = e.getBoundingClientRect();
        if (!q.width || q.right <= W + 1 || e.closest('[hidden]')) continue;
        // cut off by an ancestor that clips or scrolls and itself fits: not visible, no overflow of the page
        let clipped = false;
        for (let a = e.parentElement; a && a !== document.body; a = a.parentElement) {
          if (getComputedStyle(a).overflowX !== 'visible' && a.getBoundingClientRect().right <= W + 1) { clipped = true; break; }
        }
        if (!clipped) out.push(`${e.tagName.toLowerCase()}#${e.id}.${[...e.classList].join('.')} r=${Math.round(q.right)} w=${Math.round(q.width)}`);
      }
      return { iw: innerWidth, sw: document.documentElement.scrollWidth, vv: Math.round(visualViewport.width), out: out.slice(0, 12), video: document.querySelector('#video').readyState };
    }, WIDTH);
    ok(`${cut}: the page does not scroll sideways`, r.video >= 1 && r.iw === WIDTH && r.sw <= WIDTH && r.vv === WIDTH, JSON.stringify({ iw: r.iw, sw: r.sw, vv: r.vv }));
    ok(`${cut}: nothing sticks out on the right`, r.out.length === 0, r.out.join('; '));
    await screenshot(p, `width_${cut}`, { fullPage: true });
  }
  ok('No errors in the browser', errors.length === 0, errors.join(' | '));
  await browser.close();
  done();
});
