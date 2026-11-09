'use strict';
// Browser test of the review app (Chrome headless via playwright-core): video and HTTP ranges, timeline, shortcuts,
// comments, a second reviewer, status and export via the API, edit preview, J/K/L, preview image, phone with touch.
// Runs against the synthetic test data of make_fixture.py with an empty comment database (cut4 by default).
// BASE=http://127.0.0.1:8295/ PWFILE=... [USER_NAME=tester] [CUT=cut4] [SHOTS=dir] node test_app.js
const fs = require('fs');
const path = require('path');
const { BASE, SHOTS, credentials, IPHONE_UA, launch, checks, main, sleep, waitUntil, watch, videoState, timeline, screenshot } = require('./common');

const CUT = process.env.CUT || 'cut4';
const A = 'Reviewer A', B = 'Reviewer B', PHONE_NAME = 'Reviewer Phone';
const isTest = (person) => person.startsWith('Reviewer');
const { ok, done } = checks();

// waits until a seek is finished and the picture is shown, returns a checksum of the picture
async function frameChecksum(page) {
  return page.evaluate(() => new Promise((resolve) => {
    const v = document.querySelector('#video');
    const finished = () => requestAnimationFrame(() => requestAnimationFrame(() => {
      const c = document.createElement('canvas'); c.width = 160; c.height = 90;
      const g = c.getContext('2d'); g.drawImage(v, 0, 0, 160, 90);
      const d = g.getImageData(0, 0, 160, 90).data; let h = 0;
      for (let i = 0; i < d.length; i += 4) h = (h * 31 + d[i] + 2 * d[i + 1] + 3 * d[i + 2]) >>> 0;
      resolve({ h, t: v.currentTime, frame: Math.floor(v.currentTime * 25 + 1e-3) });
    }));
    if (v.seeking) v.addEventListener('seeked', finished, { once: true }); else finished();
  }));
}
async function api(page, method, url, body) {
  return page.evaluate(async ([method, url, body]) => {
    const r = await fetch(url, { method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
    const ct = r.headers.get('content-type') || '';
    return { status: r.status, headers: Object.fromEntries(r.headers), body: ct.includes('json') ? await r.json() : await r.text() };
  }, [method, url, body]);
}
async function setName(page, name) {
  await page.waitForSelector('#name-dialog[open]', { timeout: 15000 });
  await page.fill('#name-input', name);
  await page.click('#name-form button[type=submit]');
  await waitUntil(page, () => !document.querySelector('#name-dialog').open);
}
const setTime = (page, t) => page.evaluate((x) => { document.querySelector('#video').currentTime = x; }, t);
const seconds = (m) => +m[1] * 60 + +m[2];

main(async () => {
  const browser = await launch();
  const ctxA = await browser.newContext({ viewport: { width: 1440, height: 900 }, httpCredentials: credentials, locale: 'en-US' });
  const page = await ctxA.newPage();
  const errors = [];
  watch(page, errors, '', ['status of 403']);   // the 403 is intended below
  const videoResponses = [];
  page.on('response', (r) => { if (r.url().includes('review.mp4')) videoResponses.push({ s: r.status(), cr: r.headers()['content-range'] }); });

  await page.goto(`${BASE}?f=${CUT}`, { waitUntil: 'domcontentloaded' });
  await setName(page, A);
  await waitUntil(page, () => document.querySelector('#video').readyState >= 1, null, 30000);
  const tl = await timeline(page, CUT);
  const shotAt = (x) => tl.shots.filter((s) => s.start <= x + 1e-6).pop();
  const nearestEdit = (x) => tl.edit_points.reduce((a, c) => (Math.abs(c.t - x) < Math.abs(a.t - x) ? c : a));
  let v = await videoState(page);
  ok('Video loaded, duration', Math.abs(v.d - tl.duration) < 0.5, `${v.d.toFixed(2)} s, timeline ${tl.duration} s`);
  const title = await page.textContent('#film-title');
  ok('Title', title === tl.title && title.includes('Cut'), title);

  // range request directly
  const rng = await page.evaluate(async (f) => {
    const r = await fetch(`media/${f}/review.mp4`, { headers: { Range: 'bytes=1000-1999' } }); const b = await r.arrayBuffer();
    return { s: r.status, len: b.byteLength, cr: r.headers.get('content-range'), ar: r.headers.get('accept-ranges') };
  }, CUT);
  ok('Range request 206', rng.s === 206 && rng.len === 1000 && /^bytes 1000-1999\//.test(rng.cr), JSON.stringify(rng));

  // seek with a click on the timeline (70 %)
  const box = await page.locator('#tl-canvas').boundingBox();
  await page.mouse.click(box.x + box.width * 0.7, box.y + 45);
  let b0 = await frameChecksum(page);
  v = await videoState(page);
  ok('Seek via the timeline', Math.abs(v.t - 0.7 * v.d) < 1.5 && v.rs >= 2, `t=${v.t.toFixed(2)} readyState=${v.rs}`);
  ok('Video reloaded by range request (206)', videoResponses.some((x) => x.s === 206), JSON.stringify(videoResponses.slice(-3)));
  // a jump via the timeline can be undone with "Back"
  let tm = await page.textContent('#toast-text');
  const jm = tm.match(/^Jump from 0:00\.0 to (\d+):(\d\d\.\d)$/);
  ok('Message after the jump with "Back"', !!jm && Math.abs(seconds(jm) - v.t) < 0.1 && await page.locator('#toast-button').isVisible(), tm);
  await page.click('#toast-button');
  await frameChecksum(page);
  v = await videoState(page);
  ok('"Back" returns to the old position', v.t < 0.1, `t=${v.t.toFixed(2)}`);

  // jump far and play
  await setTime(page, 37.0);
  await frameChecksum(page);
  await page.keyboard.press('Space');
  await sleep(1600);
  v = await videoState(page);
  ok('Play (Space)', !v.paused && v.t > 37.8, `t=${v.t.toFixed(2)}`);
  await page.keyboard.press('Space');
  await sleep(200);
  v = await videoState(page);
  ok('Pause (Space)', v.paused, `t=${v.t.toFixed(2)}`);
  // typing without clicking into the note field: the text lands in the field, no shortcuts (S, Z …), the video stays
  const beforeTyping = await videoState(page);
  await page.evaluate(() => document.activeElement && document.activeElement.blur());
  await page.keyboard.type('The sentence');
  const typed = await page.evaluate(() => ({ value: document.querySelector('#note').value, focus: document.activeElement.id,
    zoom: document.querySelector('#zoom-button').classList.contains('on'), loop: document.querySelector('#loop-button').classList.contains('on') }));
  v = await videoState(page);
  ok('Typing without a click lands in the note field', typed.value === 'The sentence' && typed.focus === 'note' && !typed.zoom && !typed.loop
    && v.paused && Math.abs(v.t - beforeTyping.t) < 0.05, JSON.stringify(typed));
  await page.fill('#note', '');
  await page.keyboard.press('Escape');

  // single frames forward and back
  b0 = await frameChecksum(page);
  const steps = [];
  for (let i = 0; i < 3; i++) { await page.keyboard.press('.'); steps.push(await frameChecksum(page)); }
  const different = steps.filter((s, i) => s.h !== (i ? steps[i - 1].h : b0.h)).length;
  ok('Frame forward (3× .)', steps[2].frame === b0.frame + 3 && different >= 2,
    `frame ${b0.frame} -> ${steps.map((s) => s.frame).join(',')}, ${different}/3 pictures different`);
  for (let i = 0; i < 3; i++) await page.keyboard.press(',');
  const backAgain = await frameChecksum(page);
  ok('Frame back (3× ,)', backAgain.frame === b0.frame && backAgain.h === b0.h, `frame ${backAgain.frame}, same picture as before: ${backAgain.h === b0.h}`);
  await page.click('[data-cmd=frameFwd]');
  const k1 = await frameChecksum(page);
  ok('Button "1 f ›"', k1.frame === b0.frame + 1, `frame ${k1.frame}`);

  // jumps and shots
  const tBefore = (await videoState(page)).t;
  await page.keyboard.press('ArrowLeft'); await frameChecksum(page);
  v = await videoState(page);
  ok('← jumps 2 s back', Math.abs(v.t - (tBefore - 2)) < 0.05, `${tBefore.toFixed(2)} -> ${v.t.toFixed(2)}`);
  await page.keyboard.press('Shift+ArrowRight'); await frameChecksum(page);
  v = await videoState(page);
  const next = tl.shots.find((s) => s.start > tBefore - 2 + 0.001);
  ok('Shift+→ next shot', Math.abs(v.t - (next.start + 0.02)) < 0.03, `Nr. ${next.nr} start ${next.start}, t=${v.t.toFixed(3)}`);
  const infoNr = await page.textContent('#info-nr');
  ok('Info box shows the Nr.', infoNr.trim() === `Nr. ${next.nr}`, infoNr);
  await page.keyboard.press('Shift+ArrowLeft'); await frameChecksum(page);
  v = await videoState(page);
  ok('Shift+← previous shot', v.t < next.start, `t=${v.t.toFixed(3)}`);

  // comments
  const ids = [];
  await setTime(page, 155.0);
  await frameChecksum(page);
  await page.click('#quick button[data-action=shorter]');
  await page.waitForSelector('.entry');
  let list = await page.$$eval('.entry', (els) => els.map((e) => e.textContent));
  ok('Quick button "Shorter"', list.some((t) => t.includes('Shorter') && t.includes(`Nr. ${shotAt(155).nr}`)), list[0]);
  // typing a note pauses
  await page.keyboard.press('Space'); await sleep(400);
  await page.keyboard.press('n');
  await page.keyboard.type('Test note please ignore');
  v = await videoState(page);
  ok('N + typing pauses', v.paused);
  await page.keyboard.press('Enter');
  await waitUntil(page, () => [...document.querySelectorAll('.entry')].some((e) => e.textContent.includes('Test note')));
  ok('Free text note saved', true);
  // range I/O + X
  await setTime(page, 200.0); await frameChecksum(page);
  await page.keyboard.press('i');
  await setTime(page, 203.5); await frameChecksum(page);
  await page.keyboard.press('o');
  const rangeText = await page.textContent('#range');
  await page.keyboard.press('x');
  await waitUntil(page, () => [...document.querySelectorAll('.entry')].some((e) => e.textContent.includes('to 3:23.5')));
  ok('Range I/O + X', true, rangeText);
  // cut later + reaction (the edit at 64.84 is fixed in make_fixture.py)
  await setTime(page, 64.5); await frameChecksum(page);
  const editInfo = await page.textContent('#info-edit');
  await page.click('#quick button[data-action=cut_later]');
  await page.click('#reactions button:first-child');
  await sleep(500);
  const all = (await api(page, 'GET', `api/entries?cut=${CUT}`)).body.entries;
  for (const e of all) if (isTest(e.person)) ids.push(e.id);
  const later = all.find((e) => e.action === 'cut_later');
  const e64 = nearestEdit(64.5);
  ok('Cut later with the edit point', later && later.edit_point && Math.abs(e64.t - 64.84) < 0.001 && Math.abs(later.edit_point.t - e64.t) < 0.01
    && later.edit_point.before === e64.before && later.edit_point.after === e64.after && later.edit_point.kind === 'shot',
  `${editInfo} -> ${JSON.stringify(later && later.edit_point)}`);
  ok('Reaction 😂', all.some((e) => e.reaction === '😂'));
  const rm = all.find((e) => e.action === 'remove');
  ok('Entry enriched (Nr., clip, source time, section)', rm && rm.t_to === 203.5 && rm.nr === shotAt(200).nr && rm.clip && rm.source_t != null && rm.section,
    JSON.stringify(rm && { t: rm.t, t_to: rm.t_to, nr: rm.nr, nr_to: rm.nr_to, clip: rm.clip, source_t: rm.source_t, section: rm.section }));

  // edit
  await page.click('.entry .tools button[title="Change"]');
  await page.fill('#edit-text', 'changed test text');
  await page.click('#edit-form button[value=ok]');
  await waitUntil(page, () => [...document.querySelectorAll('.entry')].some((e) => e.textContent.includes('changed test text')));
  ok('Edit an own entry', true);
  // a click in the list jumps, "Back" returns
  await setTime(page, 300.0); await frameChecksum(page);
  await page.click('.entry:has-text("Shorter") .content');
  await frameChecksum(page);
  v = await videoState(page);
  tm = await page.textContent('#toast-text');
  ok('List: a click jumps, message with "Back"', Math.abs(v.t - 155) < 0.05 && tm.startsWith('Jump from 5:00.0 to 2:35.0'), `t=${v.t.toFixed(2)}, ${tm}`);
  await page.click('#toast-button'); await frameChecksum(page);
  v = await videoState(page);
  ok('List: "Back"', Math.abs(v.t - 300) < 0.05, `t=${v.t.toFixed(2)}`);

  // a second reviewer sees everything, A sees B
  const ctxB = await browser.newContext({ viewport: { width: 1280, height: 800 }, httpCredentials: credentials, locale: 'en-US' });
  const pageB = await ctxB.newPage();
  await pageB.goto(`${BASE}?f=${CUT}`, { waitUntil: 'domcontentloaded' });
  await setName(pageB, B);
  await waitUntil(pageB, (n) => document.querySelectorAll('.entry').length >= n, ids.length, 8000);
  const bTools = await pageB.$$eval('.entry .tools button', (x) => x.length);
  ok('B sees the entries of A, without edit buttons', bTools === 0, `${ids.length} entries`);
  await setTime(pageB, 300.0);
  await pageB.click('#quick button[data-action=question]');
  const t0 = Date.now();
  await waitUntil(page, (b) => [...document.querySelectorAll('.entry')].some((e) => e.textContent.includes(b)), B, 8000);
  const toast = await page.textContent('#toast-text');
  ok('A sees the new entry of B right away', true, `${((Date.now() - t0) / 1000).toFixed(1)} s, message: ${toast}`);
  const ofB = (await api(pageB, 'GET', `api/entries?cut=${CUT}`)).body.entries.find((e) => e.person === B);
  const foreign = await api(page, 'DELETE', `api/entries/${ofB.id}?person=${encodeURIComponent(A)}`);
  ok('Deleting an entry of someone else is forbidden (403)', foreign.status === 403, JSON.stringify(foreign.body));

  // status via the API
  const st = await api(page, 'POST', 'api/status', { ids: [ids[0]], status: 'done', reply: 'Test reply: shortened in cut5' });
  await waitUntil(page, () => [...document.querySelectorAll('.entry')].some((e) => e.textContent.includes('✓ done')), null, 8000);
  ok('Status "done" via the API, the app shows it', st.status === 200 && st.body.changed === 1);
  await page.selectOption('#filter-status', 'done');
  const onlyDone = await page.$$eval('.entry', (x) => x.length);
  await page.selectOption('#filter-person', B);
  const onlyB = await page.$$eval('.entry', (x) => x.length);
  await page.selectOption('#filter-status', '');
  const onlyB2 = await page.$$eval('.entry', (x) => x.length);
  await page.selectOption('#filter-person', '');
  ok('Filter status/person', onlyDone === 1 && onlyB === 0 && onlyB2 === 1, `done=${onlyDone}, B+done=${onlyB}, B=${onlyB2}`);

  // export
  const ex = await api(page, 'GET', `api/export?cut=${CUT}`);
  const e0 = ex.body.entries[0];
  const fields = ['id', 't', 't_to', 'nr', 'clip', 'section', 'action', 'text', 'reaction', 'person', 'status', 'created'];
  ok('Export JSON with all fields', ex.status === 200 && fields.every((k) => k in e0) && ex.body.people.length === 2 && ex.body.cut === CUT,
    `${ex.body.entries.length} entries, people ${ex.body.people.map((p) => p.name).join(', ')}`);
  const md = await api(page, 'GET', `api/export?cut=${CUT}&format=md`);
  ok('Export Markdown', md.status === 200 && md.body.startsWith(`# Feedback ${CUT}`) && md.headers['content-type'].includes('markdown'), md.body.split('\n')[0]);
  if (SHOTS) {
    fs.writeFileSync(path.join(SHOTS, 'export.md'), md.body);
    fs.writeFileSync(path.join(SHOTS, 'export.json'), JSON.stringify(ex.body, null, 1));
  }

  // edit preview and J/K (the edit at 97.24 is fixed in make_fixture.py)
  await setTime(page, 97.0); await frameChecksum(page);
  const e97 = nearestEdit(97.0);
  await page.keyboard.press('s');
  await sleep(5200);
  v = await videoState(page);
  const loopHint = await page.textContent('#video-hint');
  ok('Edit preview in a loop (S)', Math.abs(e97.t - 97.24) < 0.001 && !v.paused && v.t >= e97.t - 2.05 && v.t <= e97.t + 2.2, `${loopHint}, t=${v.t.toFixed(2)} after 5.2 s`);
  await screenshot(page, 'desktop_loop');
  await page.keyboard.press('Escape');
  await page.keyboard.press('k');
  await setTime(page, 250.0); await frameChecksum(page);
  await page.keyboard.press('j');
  await sleep(1500);
  await page.keyboard.press('k');
  v = await videoState(page);
  ok('J plays backwards, K stops', v.t < 249.5 && v.t > 247, `t=${v.t.toFixed(2)}`);
  await page.keyboard.press('l'); await sleep(800); await page.keyboard.press('l'); await sleep(300);
  const rate = await page.evaluate(() => document.querySelector('#video').playbackRate);
  await page.keyboard.press('k');
  ok('L forwards, L twice = 1.5×', rate === 1.5, `speed ${rate}`);
  await page.click('[data-rate="0.5"]');
  ok('Speed button 0.5×', (await page.evaluate(() => document.querySelector('#video').playbackRate)) === 0.5);
  await page.click('[data-rate="1"]');

  // preview image while hovering
  await page.locator('#tl-canvas').scrollIntoViewIfNeeded();
  const box2 = await page.locator('#tl-canvas').boundingBox();
  await page.mouse.move(box2.x + box2.width * 0.4, box2.y + 40);
  await sleep(300);
  const pv = await page.evaluate(() => ({ visible: !document.querySelector('#tl-preview').hidden, image: getComputedStyle(document.querySelector('#tl-image')).backgroundImage,
    pos: getComputedStyle(document.querySelector('#tl-image')).backgroundPosition, text: document.querySelector('#tl-text').textContent }));
  ok('Preview image while hovering', pv.visible && pv.image.includes('thumbs_') && pv.text.includes('Nr.'), `${pv.text} ${pv.pos}`);
  await screenshot(page, 'desktop_preview');
  await page.keyboard.press('z');
  await sleep(200);
  await screenshot(page, 'desktop_zoom');
  await page.keyboard.press('z');
  await page.keyboard.press('?');
  await page.waitForSelector('#help-dialog[open]');
  await screenshot(page, 'desktop_help');
  await page.keyboard.press('Escape');

  // phone
  const ctxM = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
    httpCredentials: credentials, locale: 'en-US', userAgent: IPHONE_UA });
  const m = await ctxM.newPage();
  watch(m, errors, 'phone: ');
  await m.goto(`${BASE}?f=${CUT}`, { waitUntil: 'domcontentloaded' });
  await setName(m, PHONE_NAME);
  await waitUntil(m, () => document.querySelector('#video').readyState >= 1, null, 30000);
  await setTime(m, 100.0);
  await frameChecksum(m);
  const vb = await m.locator('#tap-area').boundingBox();
  await m.touchscreen.tap(vb.x + vb.width * 0.8, vb.y + vb.height / 2); await sleep(120);
  await m.touchscreen.tap(vb.x + vb.width * 0.8, vb.y + vb.height / 2);
  await frameChecksum(m); await sleep(150);
  let mv = await videoState(m);
  ok('Phone: double tap right +5 s', Math.abs(mv.t - 105) < 0.1 && mv.paused, `t=${mv.t.toFixed(2)}`);
  await m.touchscreen.tap(vb.x + vb.width * 0.2, vb.y + vb.height / 2); await sleep(120);
  await m.touchscreen.tap(vb.x + vb.width * 0.2, vb.y + vb.height / 2);
  await frameChecksum(m); await sleep(150);
  mv = await videoState(m);
  ok('Phone: double tap left −5 s', Math.abs(mv.t - 100) < 0.1 && mv.paused, `t=${mv.t.toFixed(2)}`);
  await m.touchscreen.tap(vb.x + vb.width * 0.5, vb.y + vb.height / 2);
  await sleep(900);
  mv = await videoState(m);
  ok('Phone: single tap plays', !mv.paused, `t=${mv.t.toFixed(2)}`);
  await m.evaluate(() => document.querySelector('#video').pause());
  // timeline with a finger (an earlier bug: tapping a comment dot moved the position from 3:18 to 7:22 without anyone
  // noticing): tapping does not jump, dragging horizontally scrubs, "Back" returns
  await setTime(m, 197.9); await frameChecksum(m);
  await m.locator('#tl-canvas').scrollIntoViewIfNeeded();
  const cb = await m.locator('#tl-canvas').boundingBox();
  const xAt = (t) => cb.x + cb.width * t / tl.duration;
  await m.touchscreen.tap(xAt(0.88 * tl.duration), cb.y + 40); await sleep(400);
  mv = await videoState(m);
  const bubble = await m.evaluate(() => ({ visible: !document.querySelector('#tl-preview').hidden, text: document.querySelector('#tl-text').textContent }));
  ok('Phone: a tap on the timeline does not jump', Math.abs(mv.t - 197.9) < 0.01 && bubble.visible && bubble.text.includes('Drag horizontally to jump'),
    `t=${mv.t.toFixed(2)}, ${bubble.text}`);
  await m.touchscreen.tap(xAt(155), cb.y + 9); await sleep(400);
  mv = await videoState(m);
  const bubble2 = await m.textContent('#tl-text');
  ok('Phone: a tap on a comment dot shows it without jumping', Math.abs(mv.t - 197.9) < 0.01 && bubble2.includes(A), `t=${mv.t.toFixed(2)}, ${bubble2}`);
  const cdp = await ctxM.newCDPSession(m);
  const finger = (type, x, y) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y }] });
  const y0 = await m.evaluate(() => scrollY);
  await finger('touchStart', xAt(300), cb.y + 40);
  for (let i = 1; i <= 8; i++) { await finger('touchMove', xAt(300) + i, cb.y + 40 - i * 15); await sleep(16); }
  await finger('touchEnd'); await sleep(500);
  mv = await videoState(m);
  const y1 = await m.evaluate(() => scrollY);
  ok('Phone: swiping over the timeline only scrolls', Math.abs(mv.t - 197.9) < 0.01 && y1 !== y0, `t=${mv.t.toFixed(2)}, scrollY ${y0} -> ${y1}`);
  await m.locator('#tl-canvas').scrollIntoViewIfNeeded();
  const cb2 = await m.locator('#tl-canvas').boundingBox();
  const x2 = (t) => cb2.x + cb2.width * t / tl.duration;
  await finger('touchStart', x2(200), cb2.y + 40);
  for (let i = 1; i <= 12; i++) { await finger('touchMove', x2(200) + (x2(260) - x2(200)) * i / 12, cb2.y + 40); await sleep(30); }
  await finger('touchEnd'); await frameChecksum(m); await sleep(200);
  mv = await videoState(m);
  const tmM = await m.textContent('#toast-text');
  ok('Phone: dragging horizontally scrubs, message with "Back"', Math.abs(mv.t - 260) < 1.5 && tmM.startsWith('Jump from 3:17.9'), `t=${mv.t.toFixed(2)}, ${tmM}`);
  const kb = await m.locator('#toast-button').boundingBox();
  await m.touchscreen.tap(kb.x + kb.width / 2, kb.y + kb.height / 2); await frameChecksum(m);
  mv = await videoState(m);
  ok('Phone: "Back" after dragging', Math.abs(mv.t - 197.9) < 0.05, `t=${mv.t.toFixed(2)}`);
  const sw = await m.evaluate(() => document.documentElement.scrollWidth);
  ok('Phone: no horizontal scrolling', sw <= 390, `scrollWidth=${sw}`);
  await screenshot(m, 'phone_top');
  await screenshot(m, 'phone_full', { fullPage: true });
  await screenshot(page, 'desktop');

  // clean up: delete all test entries
  const rest = (await api(page, 'GET', `api/entries?cut=${CUT}`)).body.entries;
  let gone = 0;
  for (const e of rest) {
    if (!isTest(e.person)) continue;
    const r = await api(page, 'DELETE', `api/entries/${e.id}?person=${encodeURIComponent(e.person)}`);
    if (r.status === 200) gone++;
  }
  const left = (await api(page, 'GET', `api/entries?cut=${CUT}`)).body.entries.filter((e) => isTest(e.person)).length;
  ok('Test entries deleted', left === 0, `${gone} deleted`);
  ok('No JS errors', errors.length === 0, errors.slice(0, 5).join(' | '));
  await browser.close();
  done();
});
