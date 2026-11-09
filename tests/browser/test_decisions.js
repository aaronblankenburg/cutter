'use strict';
// Browser test: the agent's decisions (changes since the previous cut) as cards in the comment list, brackets in the
// timeline, info box, "Comment on this" (desktop and iPhone with touch). Port of the test of the former decisions tab:
// the tab is gone, the cards stand in the comment list; the checks of the tab became checks of the list and its filters.
// Run only against the synthetic test data with an empty comment database (creates a comment and deletes it again).
// BASE=http://127.0.0.1:8295/ PWFILE=... [SHOTS=dir] node test_decisions.js
const { BASE, credentials, PHONE, launch, checks, main, sleep, waitUntil, watch, videoState, seekTo, visible, text, pixel, violet,
  timeline, screenshot } = require('./common');

const { ok, done } = checks();
const settled = async (p) => { await waitUntil(p, () => !document.querySelector('#video').seeking); };
const count = (p, sel) => p.$$eval(sel, (x) => x.length);

main(async () => {
  const browser = await launch();
  const errors = [];

  // ---------------------------------------------------------------- desktop
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, httpCredentials: credentials, locale: 'en-US' });
  const p = await ctx.newPage(); watch(p, errors);
  await p.addInitScript(() => { if (!localStorage.getItem('sf_name')) localStorage.setItem('sf_name', 'Reviewer A'); });
  await p.goto(`${BASE}?f=cut5`, { waitUntil: 'domcontentloaded' });
  await waitUntil(p, () => document.querySelector('#video').readyState >= 1, null, 30000);
  await waitUntil(p, () => document.querySelectorAll('.agent-note').length > 0);
  const tl = await timeline(p, 'cut5');
  const E = Object.fromEntries(tl.decisions.entries.map((d) => [d.id, d]));
  const n = tl.decisions.entries.length, nTimed = tl.decisions.entries.filter((d) => d.t != null).length;

  ok('First visit: the decisions are cards in the comment list (no tabs), filter visible', await visible(p, '#entries') && await visible(p, '#filter')
    && !(await p.$('[role=tab], [role=tablist]')) && await visible(p, '.agent-note'));
  const list = await p.evaluate(() => [...document.querySelectorAll('#entries > li.agent-note')].map((li) => ({
    id: li.dataset.note, label: li.querySelector('.agent-label').textContent, time: (li.querySelector('.note-time') || {}).textContent || '',
    question: !!li.querySelector('.note-head .note-badge'), refs: li.querySelectorAll('.note-refs li').length })));
  ok('All decisions in the list', list.length === n && n === 13, `${list.length} of ${n}`);
  ok('General first', list[0].label === '◆ Claude · general' && list[1].label === '◆ Claude · general' && !list[0].time && !list[1].time
    && list[2].time.startsWith('▶ 0:23.1'), list.slice(0, 3).map((x) => x.time || x.label).join(' | '));
  ok('5 questions marked', list.filter((x) => x.question).length === 5, list.filter((x) => x.question).map((x) => x.id).join(','));
  ok('Count in the legend', (await text(p, '#legend')).includes(`Change by Claude (${nTimed})`), await text(p, '#legend'));
  const card = await p.evaluate(() => {
    const li = document.querySelector('.agent-note[data-note="stairs"]'), t = (s) => (li.querySelector(s) || {}).textContent || '';
    return { title: t('.note-title'), text: t('.note-text'), why: t('.note-why'), question: t('.note-question'), refs: t('.note-refs') };
  });
  ok('The card explains the change: text, why, question, reference to cut4', card.title === E.stairs.title && card.text === E.stairs.text
    && card.why === `Why: ${E.stairs.why}` && card.question === `Question for you: ${E.stairs.question}` && card.refs.includes('Reviewer B')
    && card.refs.includes(' cut4 at 0:58.2: '), JSON.stringify(card));

  // ▶ jumps to the place, with "Back"
  await p.click('.agent-note[data-note="stairs"] .note-time');
  await settled(p);
  let v = await videoState(p);
  ok('▶ jumps to the start of the decision', Math.abs(v.t - (E.stairs.t + 0.02)) < 0.03 && v.paused, `t=${v.t.toFixed(3)} expected ${E.stairs.t}`);
  ok('Message with "Back"', /^Jump from 0:00\.0 to 0:54\.8$/.test(await p.textContent('#toast-text')) && await visible(p, '#toast-button'), await p.textContent('#toast-text'));
  ok('Info box names the decision', (await text(p, '#info-now')).includes(E.stairs.title) && (await text(p, '#info-now')).includes('Change by Claude since cut4'),
    await text(p, '#info-now'));
  const cls = await p.getAttribute('.agent-note[data-note="stairs"]', 'class');
  ok('Card marked (here, selected)', cls.includes('here') && cls.includes('selected'), cls);
  ok('Bracket in the timeline (active)', violet(await pixel(p, 60, 15)), JSON.stringify(await pixel(p, 60, 15)));
  ok('No bracket without a decision', !violet(await pixel(p, 15, 15)), JSON.stringify(await pixel(p, 15, 15)));
  const m3px = await pixel(p, 260, 14);
  ok('Bracket at M3 (not active, paler)', m3px[3] > 60 && m3px[3] < 200 && m3px[2] > 200, JSON.stringify(m3px));
  await screenshot(p, 'decisions_desktop_stairs');
  await p.click('#toast-button');
  await settled(p);
  v = await videoState(p);
  ok('"Back" to the old position', v.t < 0.1, `t=${v.t.toFixed(3)}`);
  ok('Info box without a decision at 0:00', !(await visible(p, '#info-now')));

  // the info box button shows the explanation without jumping
  await seekTo(p, 98.0);
  ok('Info box at 1:38: sharper take', (await text(p, '#info-now')).includes(E.focus.title), await text(p, '#info-now'));
  await p.evaluate(() => { document.querySelector('#entries').scrollTop = 0; });
  await p.click('#info-now .now-note');
  const inView = () => {
    const li = document.querySelector('.agent-note[data-note="focus"]'), c = document.querySelector('#entries');
    const a = li.getBoundingClientRect(), b = c.getBoundingClientRect();
    return a.top >= b.top - 1 && a.top <= b.top + 20 && a.bottom <= b.bottom + 1;
  };
  try { await waitUntil(p, inView, null, 3000); } catch (e) { /* evaluated below */ }
  v = await videoState(p);
  const seen = { inside: await p.evaluate(inView), selected: await p.evaluate(() => document.querySelector('.agent-note[data-note="focus"]').classList.contains('selected')) };
  ok('Info box button: card visible and selected, the position stays', seen.inside && seen.selected && Math.abs(v.t - 98.0) < 0.05, JSON.stringify(seen) + ` t=${v.t.toFixed(2)}`);

  // preview while hovering over the timeline
  const cb = await p.locator('#tl-canvas').boundingBox();
  await p.mouse.move(cb.x + cb.width * (130 / tl.duration), cb.y + 15);
  await sleep(150);
  const pv = (await p.textContent('#tl-preview')).replace(/\s+/g, ' ').trim();
  ok('Preview names the decision', pv.includes('◆ ' + E.market.title), pv);
  await p.mouse.move(cb.x + cb.width * 0.5, cb.y - 60);

  // "Comment on this": jumps there, note with the title; saving creates the comment at that place
  await p.click('.agent-note[data-note="theme"] .note-foot button');
  await settled(p);
  v = await videoState(p);
  const note = await p.evaluate(() => ({ value: document.querySelector('#note').value, focus: document.activeElement.id }));
  ok('"Comment on this" jumps to the start', Math.abs(v.t - (E.theme.t + 0.02)) < 0.03, `t=${v.t.toFixed(3)}`);
  ok('The note starts with the title, focus in the field', note.value === `On “${E.theme.title}”: ` && note.focus === 'note', JSON.stringify(note));
  await p.keyboard.type('fits well');
  await p.keyboard.press('Enter');
  await waitUntil(p, () => document.querySelector('#note').value === '');
  const created = await p.evaluate(() => fetch('api/entries?cut=cut5').then((r) => r.json()));
  const mine = created.entries.find((e) => e.text === 'On “New music for the workshop: Test Theme C”: fits well');
  ok('Comment saved at the place of the decision', !!mine && Math.abs(mine.t - (E.theme.t + 0.02)) < 0.05 && mine.music && mine.music.cue === 'M3',
    mine ? `t=${mine.t} nr=${mine.nr} music=${mine.music && mine.music.cue}` : 'missing');
  // already inside the decision: no jump, existing text stays behind the new start
  await p.evaluate(() => { document.querySelector('#video').currentTime = 250.0; });
  await settled(p);
  await p.fill('#note', 'On “Map: overview first, then zoom in”: old');
  await p.click('.agent-note[data-note="theme"] .note-foot button');
  await sleep(200);
  v = await videoState(p);
  ok('"Comment on this" inside the place: no jump, the title replaced', Math.abs(v.t - 250) < 0.05
    && (await p.inputValue('#note')) === `On “${E.theme.title}”: old`, `t=${v.t.toFixed(2)} "${await p.inputValue('#note')}"`);
  await p.fill('#note', '');

  // a status filter shows only the comments (the cards have no status); after reloading the filter stays
  await p.selectOption('#filter-status', 'open');
  ok('Status filter "open": only the comments, the cards are hidden', await visible(p, '#filter') && (await count(p, '#entries > li.agent-note')) === 0
    && (await count(p, '#entries > li.entry')) === 1);
  await p.reload({ waitUntil: 'domcontentloaded' });
  await waitUntil(p, () => document.querySelector('#video').readyState >= 1, null, 30000);
  await waitUntil(p, () => document.querySelectorAll('#entries > li.entry').length === 1);
  const afterReload = { status: await p.inputValue('#filter-status'), cards: await count(p, '#entries > li.agent-note'), legend: await visible(p, '#legend') };
  await p.selectOption('#filter-status', '');
  afterReload.cardsAll = await count(p, '#entries > li.agent-note');
  ok('After reloading: the filter stays, "all statuses" brings the cards back', afterReload.status === 'open' && afterReload.cards === 0 && afterReload.legend
    && afterReload.cardsAll === n, JSON.stringify(afterReload));
  if (mine) await p.evaluate((id) => fetch(`api/entries/${id}?person=Reviewer%20A`, { method: 'DELETE' }), mine.id);

  // cut4 has no decisions: no cards, no bracket, no legend, the list as before
  await p.selectOption('#cut-select', 'cut4');
  await waitUntil(p, () => document.title.includes('cut4'));
  await waitUntil(p, () => document.querySelector('#video').readyState >= 1, null, 30000);
  await p.evaluate(() => { document.querySelector('#video').currentTime = 60; });
  await settled(p); await sleep(150);
  ok('cut4: no cards, no bracket, no hint, no legend', (await count(p, '.agent-note')) === 0 && await visible(p, '#filter')
    && !(await visible(p, '#info-now')) && !violet(await pixel(p, 60, 15)) && !(await visible(p, '#legend'))
    && !(await p.$('#filter-person option[value="Claude"]')));
  await p.selectOption('#cut-select', 'cut5');
  await waitUntil(p, () => document.title.includes('cut5'));
  await waitUntil(p, () => document.querySelectorAll('.agent-note').length > 0);
  ok('Back on cut5: the cards are there again', (await count(p, '#entries > li.agent-note')) === n && await visible(p, '#legend'));
  await ctx.close();

  // ---------------------------------------------------------------- iPhone (touch)
  const ctxM = await browser.newContext({ ...PHONE, httpCredentials: credentials, locale: 'en-US' });
  const m = await ctxM.newPage(); watch(m, errors, 'iPhone: ');
  await m.addInitScript(() => localStorage.setItem('sf_name', 'Reviewer Phone'));
  await m.goto(`${BASE}?f=cut5`, { waitUntil: 'domcontentloaded' });
  await waitUntil(m, () => document.querySelector('#video').readyState >= 1, null, 30000);
  await waitUntil(m, () => document.querySelectorAll('.agent-note').length > 0);
  ok('iPhone: the decisions are cards in the list', await visible(m, '#entries .agent-note') && (await count(m, '#entries > li.agent-note')) === n);
  const width = await m.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: innerWidth }));
  ok('iPhone: nothing sticks out sideways', width.sw <= width.iw, JSON.stringify(width));
  await screenshot(m, 'decisions_iphone_full', { fullPage: true });

  // a tap on the bracket in the timeline only shows the preview, it does not jump
  await m.evaluate(() => scrollTo(0, 0));
  const c = await m.locator('#tl-canvas').boundingBox();
  const before = await videoState(m);
  await m.touchscreen.tap(c.x + c.width * (60 / tl.duration), c.y + 15); await sleep(500);
  const after = await videoState(m);
  const mv = (await m.textContent('#tl-preview')).replace(/\s+/g, ' ').trim();
  ok('iPhone: a tap on the bracket does not jump', Math.abs(after.t - before.t) < 0.01, `${before.t.toFixed(2)} -> ${after.t.toFixed(2)}`);
  ok('iPhone: the preview names the decision', mv.includes('◆ ' + E.stairs.title) && mv.includes('Drag horizontally to jump'), mv);

  // tapping ▶ in the list
  const hideToast = () => m.evaluate(() => { document.querySelector('#toast').hidden = true; });
  const play = m.locator('.agent-note[data-note="map"] .note-time');
  await play.scrollIntoViewIfNeeded();
  const pb = await play.boundingBox();
  await m.touchscreen.tap(pb.x + pb.width / 2, pb.y + pb.height / 2);
  await settled(m);
  let mz = await videoState(m);
  ok('iPhone: ▶ jumps to the map', Math.abs(mz.t - (E.map.t + 0.02)) < 0.03, `t=${mz.t.toFixed(3)}`);
  // tapping the text of a card (reading): no jump
  await hideToast();
  await m.locator('.agent-note[data-note="market"] .note-why').scrollIntoViewIfNeeded();
  const tx = await m.locator('.agent-note[data-note="market"] .note-why').boundingBox();
  await m.touchscreen.tap(tx.x + 20, tx.y + 5); await sleep(400);
  mz = await videoState(m);
  ok('iPhone: a tap on the text does not jump', Math.abs(mz.t - (E.map.t + 0.02)) < 0.03, `t=${mz.t.toFixed(3)}`);
  // tapping "Comment on this"
  const cmt = m.locator('.agent-note[data-note="harbour"] .note-foot button');
  await cmt.scrollIntoViewIfNeeded();
  await hideToast();
  const cbx = await cmt.boundingBox();
  await m.touchscreen.tap(cbx.x + cbx.width / 2, cbx.y + cbx.height / 2);
  await settled(m); await sleep(300);
  mz = await videoState(m);
  const mn = await m.evaluate(() => ({ value: document.querySelector('#note').value, focus: document.activeElement.id }));
  ok('iPhone: "Comment on this" jumps and fills the note', Math.abs(mz.t - (E.harbour.t + 0.02)) < 0.03 && mn.value === `On “${E.harbour.title}”: ` && mn.focus === 'note',
    `t=${mz.t.toFixed(3)} ${JSON.stringify(mn)}`);
  await m.fill('#note', '');
  await m.evaluate(() => { document.activeElement.blur(); scrollTo(0, 0); document.querySelector('#video').currentTime = 120; });
  await settled(m); await sleep(300);
  await screenshot(m, 'decisions_iphone_top', { fullPage: true });
  await ctxM.close();

  ok('No errors in the browser', errors.length === 0, errors.join(' | '));
  await browser.close();
  done();
});
