'use strict';
// Browser test: a cut in a tall frame (timeline.json "frame": 9:16, a Reel) gets a tall player, on a computer and on a
// phone, without pushing the controls off the screen; a 16:9 cut keeps the wide one.
// BASE=http://127.0.0.1:8295/ PWFILE=... [SHOTS=dir] node test_frame.js
const { BASE, credentials, launch, checks, main, waitUntil, watch, screenshot } = require('./common');

const { ok, done } = checks();

async function open(browser, viewport, tall) {
  const ctx = await browser.newContext({ viewport, httpCredentials: credentials, locale: 'en-US' });
  const p = await ctx.newPage();
  const errors = []; watch(p, errors);
  await p.addInitScript(() => { if (!localStorage.getItem('sf_name')) localStorage.setItem('sf_name', 'Reviewer F'); });
  if (tall) {
    await p.route('**/media/cut6/timeline.json', async (route) => {
      const res = await route.fetch();
      const tl = await res.json();
      tl.frame = { name: '9:16', width: 1080, height: 1920 };
      await route.fulfill({ response: res, json: tl });
    });
  }
  await p.goto(`${BASE}?cut=cut6`, { waitUntil: 'domcontentloaded' });
  await waitUntil(p, () => document.querySelector('#video').readyState >= 1, null, 30000);
  const box = await p.evaluate(() => {
    const r = document.querySelector('#video-frame').getBoundingClientRect();
    const play = document.querySelector('[data-cmd="play"]') || document.querySelector('.transport button');
    return { w: r.width, h: r.height, bottom: r.bottom, controls: play ? play.getBoundingClientRect().bottom : 0 };
  });
  return { p, ctx, box, errors };
}

main(async () => {
  const browser = await launch();
  let r = await open(browser, { width: 1440, height: 900 }, false);
  ok('16:9 cut: wide player', Math.abs(r.box.w / r.box.h - 16 / 9) < 0.02, JSON.stringify(r.box));
  await r.ctx.close();

  r = await open(browser, { width: 1440, height: 900 }, true);
  ok('9:16 cut on a computer: tall player', Math.abs(r.box.w / r.box.h - 9 / 16) < 0.02, JSON.stringify(r.box));
  ok('… that fits the window with its controls', r.box.controls > 0 && r.box.controls <= 900, JSON.stringify(r.box));
  await screenshot(r.p, 'frame_tall_desktop');
  ok('… without errors', r.errors.length === 0, r.errors.join('\n'));
  await r.ctx.close();

  r = await open(browser, { width: 390, height: 844 }, true);
  ok('9:16 cut on a phone: tall player', Math.abs(r.box.w / r.box.h - 9 / 16) < 0.02, JSON.stringify(r.box));
  ok('… not taller than two thirds of the screen', r.box.h <= 844 * 0.66, JSON.stringify(r.box));
  await screenshot(r.p, 'frame_tall_phone');
  await r.ctx.close();
  await browser.close();
  done();
});
