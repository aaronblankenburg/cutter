'use strict';
// Browser test: the colour suggestion in the shot's info box (timeline.json "color"), switching it off and on for a
// shot and for the whole film (comments with "color"), the hint that the video shows the earlier setting.
// Run only against the synthetic test data with an empty comment database.
// BASE=http://127.0.0.1:8295/ PWFILE=... [SHOTS=dir] node test_color.js
const { BASE, credentials, launch, checks, main, waitUntil, watch, seekTo, visible, text, timeline, screenshot } = require('./common');

const { ok, done } = checks();
const entries = (p) => p.evaluate(() => fetch('api/entries?cut=cut6', { cache: 'no-store' }).then((r) => r.json()));

main(async () => {
  const browser = await launch();
  const errors = [];
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, httpCredentials: credentials, locale: 'en-US' });
  const p = await ctx.newPage(); watch(p, errors);
  await p.addInitScript(() => { if (!localStorage.getItem('sf_name')) localStorage.setItem('sf_name', 'Reviewer C'); });
  await p.goto(`${BASE}?cut=cut6`, { waitUntil: 'domcontentloaded' });
  await waitUntil(p, () => document.querySelector('#video').readyState >= 1, null, 30000);
  const tl = await timeline(p, 'cut6');
  const nrs = Object.keys(tl.color.shots).map(Number).sort((a, b) => a - b);
  const on = tl.shots.find((s) => s.nr === nrs.find((n) => tl.color.shots[n].on));
  const off = tl.shots.find((s) => s.nr === nrs.find((n) => !tl.color.shots[n].on));
  ok('timeline.json has a suggestion that is on and one switched off in the edit list', on && off, JSON.stringify(tl.color));

  await seekTo(p, on.start + 0.5);
  await waitUntil(p, () => !document.querySelector('#info-color').hidden);
  ok('Info box shows the suggestion', (await text(p, '#color-values')) === 'exposure +0.4 · warmth +12', await text(p, '#color-values'));
  ok('… and that it is on', (await text(p, '#color-state')).startsWith('Suggested correction: on.'), await text(p, '#color-state'));
  ok('Before/after still when it could be made', !tl.color.shots[on.nr].still || await visible(p, '#color-still'));
  await screenshot(p, 'color_on');

  await p.click('#color-shot');
  await waitUntil(p, () => document.querySelector('#color-state').textContent.startsWith('Switched off for this shot'));
  let es = (await entries(p)).entries.filter((e) => e.color);
  ok('"Off for shot" is a comment', es.length === 1 && es[0].color === `${on.nr}/off` && es[0].person === 'Reviewer C', JSON.stringify(es));
  ok('… and the video still shows the correction', (await text(p, '#color-state')).includes('The video still shows the earlier setting'));
  ok('… listed with the comments', (await text(p, '#entries')).includes(`Colour correction off for shot ${on.nr}`));
  await p.click('#color-shot');
  await waitUntil(p, () => document.querySelector('#color-state').textContent.startsWith('Suggested correction: on.'));
  es = (await entries(p)).entries.filter((e) => e.color);
  ok('Switching again changes the same comment', es.length === 1 && es[0].color === `${on.nr}/on`, JSON.stringify(es));

  await p.click('#color-all');
  await waitUntil(p, () => document.querySelector('#color-state').textContent.startsWith('Switched off for the whole film'));
  es = (await entries(p)).entries.filter((e) => e.color);
  ok('"Off for the whole film": a general comment', es.some((e) => e.color === 'all/off' && e.t == null), JSON.stringify(es));
  await seekTo(p, off.start + 0.5);
  await waitUntil(p, () => document.querySelector('#color-values').textContent.includes('blacks'));
  ok('Shot switched off in the edit list: off', (await text(p, '#color-state')).startsWith('Switched off for the whole film'));
  await p.click('#color-all');
  await waitUntil(p, () => document.querySelector('#color-state').textContent.startsWith('Switched off for this shot'));
  ok('Whole film on again: that shot stays off', true);

  await p.reload({ waitUntil: 'domcontentloaded' });
  await waitUntil(p, () => document.querySelector('#video').readyState >= 1, null, 30000);
  await seekTo(p, on.start + 0.5);
  await waitUntil(p, () => !document.querySelector('#info-color').hidden);
  ok('After a reload the switches are kept', (await text(p, '#color-state')).startsWith('Suggested correction: on.'), await text(p, '#color-state'));
  const without = tl.shots.find((s) => !tl.color.shots[s.nr] && s.start > 5);
  await seekTo(p, without.start + 0.3);
  await waitUntil(p, () => document.querySelector('#info-color').hidden);
  ok('No colour box where there is no suggestion', true);

  const bad = await p.evaluate(() => fetch('api/entries', { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ cut: 'cut6', person: 'X', color: '9999/off' }) }).then((r) => r.status));
  ok('A colour switch for a shot that does not exist is refused', bad === 400, String(bad));
  const expected = errors.filter((x) => x.includes('status of 400'));
  errors.splice(0, errors.length, ...errors.filter((x) => !expected.includes(x)));

  // clean up: the other tests expect an empty database
  for (const e of (await entries(p)).entries.filter((x) => x.person === 'Reviewer C')) {
    await p.evaluate((id) => fetch(`api/entries/${id}?person=Reviewer%20C`, { method: 'DELETE' }), e.id);
  }
  ok('No errors in the console', errors.length === 0, errors.join('\n'));
  await browser.close();
  done();
});
