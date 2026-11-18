'use strict';
// Browser test: alternatives (watch the options of a place, "Take this" / "Keep as is" as one comment per place),
// general comments about the whole film, opening a cut by ?cut=, and the frontend embedded in another page
// (project.json "person", "submit": false, "embedded": true; postMessage to the parent).
// Run only against the synthetic test data with an empty comment database; restores project.json at the end.
// BASE=http://127.0.0.1:8295/ PWFILE=... DATA=<fixture>/data [SHOTS=dir] node test_alternatives.js
const fs = require('fs');
const path = require('path');
const { BASE, credentials, launch, checks, main, sleep, waitUntil, watch, videoState, seekTo, visible, text, pixel, timeline,
  screenshot } = require('./common');

const { ok, done } = checks();
const entries = (p, cut) => p.evaluate((f) => fetch(`api/entries?cut=${f}`, { cache: 'no-store' }).then((r) => r.json()), cut);
const teal = (c) => c[3] > 200 && c[1] > 170 && c[2] > 150 && c[0] < 90;

main(async () => {
  const browser = await launch();
  const errors = [];
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, httpCredentials: credentials, locale: 'en-US' });
  const p = await ctx.newPage(); watch(p, errors);
  await p.addInitScript(() => { if (!localStorage.getItem('sf_name')) localStorage.setItem('sf_name', 'Reviewer A'); });

  // ---------------------------------------------------------------- ?cut= and the place with alternatives
  await p.goto(`${BASE}?cut=cut6`, { waitUntil: 'domcontentloaded' });
  await waitUntil(p, () => document.querySelector('#video').readyState >= 1, null, 30000);
  ok('?cut= opens that cut', (await p.inputValue('#cut-select')) === 'cut6');
  const tl = await timeline(p, 'cut6');
  const place = tl.alternatives && tl.alternatives[0];
  ok('timeline.json has the place with 3 options, two with a preview', place && place.id === 'workshop' && place.options.length === 3
    && place.options.filter((o) => o.file).length === 2 && !place.options[2].file, JSON.stringify(place && place.options.map((o) => o.file)));
  ok('No alternatives box away from the place', !(await visible(p, '#alts')));
  ok('Header button counts the places', (await text(p, '#alts-button')) === '⇄ Alternatives: 1', await text(p, '#alts-button'));
  await p.click('#alts-button');
  await waitUntil(p, () => !document.querySelector('#alts').hidden);
  ok('Header button jumps to the place', Math.abs((await videoState(p)).t - place.t) < 0.5, JSON.stringify(await videoState(p)));
  await seekTo(p, 0.2);
  await seekTo(p, place.t + 0.5);
  await waitUntil(p, () => !document.querySelector('#alts').hidden);
  ok('Box with the alternatives at the place', (await text(p, '.alts-title')).includes('(3)') && (await text(p, '.alts-reason')) === place.reason,
    await text(p, '#alts'));
  ok('Three options, one without a preview', (await p.$$eval('.alt-option', (x) => x.length)) === 3
    && (await p.$$eval('.alt-option video', (x) => x.length)) === 2 && (await text(p, '.alt-missing')) === 'No preview');
  ok('Teal line on the timeline at the place', teal(await pixel(p, (place.t + place.t_to) / 2, 21)), JSON.stringify(await pixel(p, (place.t + place.t_to) / 2, 21)));
  await screenshot(p, 'alternatives');

  // an option plays, the cut pauses
  await p.evaluate(() => document.querySelector('#video').play());
  await sleep(300);
  await p.evaluate(() => document.querySelector('.alt-option video').play());
  await waitUntil(p, () => { const v = document.querySelector('.alt-option video'); return v.currentTime > 0.3; }, null, 10000);
  const v = await videoState(p);
  ok('An option plays inline and pauses the cut', v.paused);
  const src = await p.evaluate(() => document.querySelector('.alt-option video').currentSrc);
  ok('The preview comes from the cut\'s media', src.endsWith('/media/cut6/alt-workshop-1.mp4'), src);

  // "Take this", then "Keep as is": one comment per reviewer and place
  await p.click('.alt-option:nth-child(2) button');
  await waitUntil(p, () => document.querySelector('.alt-option:nth-child(2)').classList.contains('chosen'));
  let r = await entries(p, 'cut6');
  ok('"Take this" makes a comment with the choice', r.entries.length === 1 && r.entries[0].alternative === 'workshop/2'
    && r.entries[0].action === 'other_shot' && r.entries[0].t === place.t, JSON.stringify(r.entries[0]));
  ok('The comment in the list names the option', (await text(p, '#entries .alt-info')).startsWith('Alternative 2: C900'), await text(p, '#entries .alt-info'));
  const first = r.entries[0].id;
  await p.click('.alt-keep');
  await waitUntil(p, () => document.querySelector('.alt-keep').classList.contains('save'));
  r = await entries(p, 'cut6');
  ok('"Keep as is" changes the choice instead of adding one', r.entries.length === 1 && r.entries[0].id === first
    && r.entries[0].alternative === 'workshop/keep', JSON.stringify(r.entries));
  ok('Only "Keep as is" is marked now', !(await p.$('.alt-option.chosen')));

  // ---------------------------------------------------------------- a comment about the whole film
  await seekTo(p, 30);
  await p.check('#whole');
  ok('"Whole film" changes the position line', (await text(p, '#position')) === 'Comment on the whole film', await text(p, '#position'));
  await p.fill('#note', 'Overall a bit long');
  await p.press('#note', 'Enter');
  await waitUntil(p, () => !!document.querySelector('#entries .entry.general'));
  r = await entries(p, 'cut6');
  const general = r.entries.find((e) => e.text === 'Overall a bit long');
  ok('General comment has no moment', general && general.t === null && general.nr === null, JSON.stringify(general));
  ok('General comment first in the list, labelled "Film"', await p.evaluate(() => {
    const li = document.querySelector('#entries > li');
    return li.classList.contains('general') && li.querySelector('.when').textContent === 'Film';
  }));
  ok('"Whole film" is off again after saving', !(await p.isChecked('#whole')));
  ok('Selecting the general comment does not move the video', await (async () => {
    const before = (await videoState(p)).t;
    await p.click('#entries .entry.general .content');
    await sleep(300);
    return Math.abs((await videoState(p)).t - before) < 0.05;
  })());

  // ---------------------------------------------------------------- embedded in another page
  const project = path.join(process.env.DATA, 'project.json');
  const saved = fs.readFileSync(project, 'utf8');
  try {
    fs.writeFileSync(project, JSON.stringify({ ...JSON.parse(saved), person: 'Host User', submit: false, embedded: true }));
    const host = await ctx.newPage(); watch(host, errors, 'host: ');
    await host.route(`${BASE}host.html`, (route) => route.fulfill({ contentType: 'text/html', body:
      '<!doctype html><title>host</title><link rel="icon" href="data:,"><body style="margin:0"><script>window.got=[];addEventListener("message",'
      + '(e)=>{if(e.origin===location.origin&&e.data&&e.data.source==="cutter-review")window.got.push(e.data)});</script>'
      + '<iframe id="f" src="./?cut=cut6" style="width:1400px;height:860px;border:0"></iframe></body>' }));
    await host.addInitScript(() => { try { localStorage.removeItem('sf_name'); } catch (e) { /* */ } });
    await host.goto(`${BASE}host.html`, { waitUntil: 'domcontentloaded' });
    await waitUntil(host, () => (window.got || []).some((x) => x.entries), null, 30000);
    const frame = host.frames().find((f) => f.url().includes('cut6') || f.url().includes('f=cut6'));
    ok('Embedded: the parent hears the cut and its comments', await host.evaluate(() => {
      const m = window.got.filter((x) => x.entries).pop();
      return m.cut === 'cut6' && m.entries.length === 2 && m.open === 2;
    }), JSON.stringify(await host.evaluate(() => window.got.map((m) => [m.cut, m.open, m.entries && m.entries.length, m.height]))));
    ok('Embedded: no name dialog, no name button, no "Send feedback", no brand', await frame.evaluate(() =>
      !document.querySelector('#name-dialog').open && document.querySelector('#name-button').hidden
      && document.querySelector('#send').hidden && getComputedStyle(document.querySelector('.brand')).display === 'none'));
    ok('Embedded: the layout uses the full width', await frame.evaluate(() =>
      document.querySelector('.layout').getBoundingClientRect().width >= window.innerWidth - 1));
    ok('Embedded, wide: the page fits the frame (only the list scrolls)', await frame.evaluate(() =>
      document.documentElement.scrollHeight <= window.innerHeight + 1), String(await frame.evaluate(() => document.documentElement.scrollHeight)));
    ok('Embedded, wide: the parent is told the page fits', await host.evaluate(() => window.got.some((m) => m.wide === true && m.height === 0)));
    await host.evaluate(() => { document.getElementById('f').style.width = '700px'; });
    await waitUntil(host, () => window.got.some((m) => m.wide === false && m.height > 860));
    const tall = await host.evaluate(() => window.got.filter((m) => m.wide === false).pop().height);
    ok('Embedded, narrow: the parent is told the height of the page', tall === await frame.evaluate(() =>
      Math.ceil(document.querySelector('.layout').getBoundingClientRect().bottom + window.scrollY)), String(tall));
    await host.evaluate(() => { document.getElementById('f').style.width = '1400px'; });
    await waitUntil(host, () => window.got.filter((x) => 'wide' in x).pop().wide === true);
    await frame.fill('#note', 'From the host');
    await frame.press('#note', 'Enter');
    await waitUntil(host, () => window.got.filter((x) => x.entries).pop().entries.length === 3);
    ok('Embedded: a new comment is the fixed person\'s and reaches the parent', await host.evaluate(() => {
      const m = window.got.filter((x) => x.entries).pop();
      return m.open === 3 && m.entries.some((e) => e.text === 'From the host' && e.person === 'Host User');
    }));
    await screenshot(host, 'embedded');
    await host.close();
  } finally {
    fs.writeFileSync(project, saved);
  }

  ok('No errors in the page', errors.length === 0, errors.join(' | '));
  await browser.close();
  done();
});
