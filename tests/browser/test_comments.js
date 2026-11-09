'use strict';
// Browser test: the agent's comments in the list (changes in cut6, replies under the comments in cut5), legend, filters,
// "now at this point" in the info box or below the timeline, the list following playback (desktop and iPhone).
// Run only against the synthetic test data with an empty comment database (creates comments and sets their status).
// BASE=http://127.0.0.1:8295/ PWFILE=... [SHOTS=dir] node test_comments.js
const { BASE, credentials, PHONE, launch, checks, main, sleep, waitUntil, watch, videoState, seekTo, visible, text, pixel, violet,
  timeline, screenshot } = require('./common');

const { ok, done } = checks();
// visible inside the list (on a computer the list scrolls by itself)
const inList = (p, sel) => p.evaluate((s) => {
  const li = document.querySelector(s), c = document.querySelector('#entries');
  if (!li) return false;
  const a = li.getBoundingClientRect(), b = c.getBoundingClientRect();
  return a.top >= b.top - 2 && a.top < b.bottom - 20;
}, sel);
const settled = async (p) => { await waitUntil(p, () => !document.querySelector('#video').seeking); await sleep(200); };

main(async () => {
  const browser = await launch();
  const errors = [];

  // ---------------------------------------------------------------- desktop, cut6: changes by Claude
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, httpCredentials: credentials, locale: 'en-US' });
  const p = await ctx.newPage(); watch(p, errors);
  await p.addInitScript(() => { if (!localStorage.getItem('sf_name')) localStorage.setItem('sf_name', 'Reviewer A'); });
  await p.goto(`${BASE}?f=cut6`, { waitUntil: 'domcontentloaded' });
  await waitUntil(p, () => document.querySelector('#video').readyState >= 1, null, 30000);
  await waitUntil(p, () => document.querySelectorAll('.agent-note').length > 0);
  const tl6 = await timeline(p, 'cut6');
  const E = Object.fromEntries(tl6.decisions.entries.map((d) => [d.id, d]));
  const nNotes = tl6.decisions.entries.length, nTimed = tl6.decisions.entries.filter((d) => d.t != null).length;
  const cards = await p.evaluate(() => [...document.querySelectorAll('#entries > li')].map((li) => ({
    note: li.dataset.note || null, label: (li.querySelector('.agent-label') || {}).textContent || '',
    time: (li.querySelector('.note-time') || {}).textContent || '', question: !!li.querySelector('.note-head .note-badge') })));
  ok('cut6: all comments by Claude in the list', cards.length === nNotes && cards.every((k) => k.note), `${cards.length} of ${nNotes}`);
  ok('cut6: general first, then by time', cards[0].label === '◆ Claude · general' && cards[1].label === '◆ Claude · general'
    && cards[2].note === 'map' && cards[2].time.startsWith('▶ 0:23.1') && cards[2].label === '◆ Change by Claude', JSON.stringify(cards.slice(0, 3)));
  ok('cut6: questions marked', cards.filter((k) => k.question).length === tl6.decisions.entries.filter((d) => d.question).length,
    `${cards.filter((k) => k.question).length}`);
  const leg6 = await text(p, '#legend');
  ok('cut6: legend (comment, change by Claude)', leg6.includes('Comment') && leg6.includes(`Change by Claude (${nTimed})`) && !leg6.includes('Reply'), leg6);
  ok('cut6: no separate tab, the cards are in the comment list', !(await p.$('[role=tab], [role=tablist]'))
    && (await p.$$('#entries > li.agent-note')).length === nNotes);
  ok('cut6: count without Claude (no comments)', (await text(p, '#count')) === '' && !(await visible(p, '#list-empty')));
  await screenshot(p, 'comments_desktop_cut6');

  // ▶ jumps, with "Back"; the info box shows the change with its text
  await p.click('.agent-note[data-note="pace"] .note-time');
  await settled(p);
  let v = await videoState(p);
  ok('▶ jumps to the start', Math.abs(v.t - (E.pace.t + 0.02)) < 0.03 && v.paused, `t=${v.t.toFixed(3)} expected ${E.pace.t}`);
  ok('Message with "Back"', /^Jump from 0:00\.0 to 1:19\.9$/.test(await text(p, '#toast-text')) && await visible(p, '#toast-button'), await text(p, '#toast-text'));
  const now = await text(p, '#info-now');
  ok('Info box: change with title and text', await visible(p, '#info-now') && now.includes('◆ Change by Claude since cut5') && now.includes(E.pace.title)
    && now.includes(E.pace.text.slice(0, 40)), now.slice(0, 160));
  ok('Nothing below the timeline (only on the phone)', !(await visible(p, '#now')));
  const cls = await p.getAttribute('.agent-note[data-note="pace"]', 'class');
  ok('Card marked (here, selected)', cls.includes('here') && cls.includes('selected'), cls);
  ok('Bracket in the timeline', violet(await pixel(p, E.pace.t + 1, 15)) && !violet(await pixel(p, 10, 15)));
  await p.click('#toast-button');
  await settled(p);
  v = await videoState(p);
  ok('"Back" to the old position, info box without a change', v.t < 0.1 && !(await visible(p, '#info-now')), `t=${v.t.toFixed(3)}`);

  // the info box button shows the whole explanation in the list, the position stays
  await seekTo(p, 230.0);
  await p.evaluate(() => { document.querySelector('#entries').scrollTop = 0; });
  ok('Info box at 3:50: bridge', (await text(p, '#info-now')).includes(E.bridge.title), await text(p, '#info-now'));
  await p.click('#info-now .now-note');
  try {
    await waitUntil(p, (s) => {
      const li = document.querySelector(s), c = document.querySelector('#entries'); const a = li.getBoundingClientRect(), b = c.getBoundingClientRect();
      return a.top >= b.top - 2 && a.top < b.top + 30;
    }, '.agent-note[data-note="bridge"]', 3000);
  } catch (e) { /* evaluated below */ }
  v = await videoState(p);
  ok('Info box button: card visible and selected, the position stays', await inList(p, '.agent-note[data-note="bridge"]')
    && (await p.getAttribute('.agent-note[data-note="bridge"]', 'class')).includes('selected') && Math.abs(v.t - 230) < 0.05, `t=${v.t.toFixed(2)}`);

  // preview while hovering over the timeline
  const cb = await p.locator('#tl-canvas').boundingBox();
  await p.mouse.move(cb.x + cb.width * (300 / tl6.duration), cb.y + 15); await sleep(150);
  const pv = (await text(p, '#tl-preview')).replace(/\s+/g, ' ').trim();
  ok('Preview names the change', pv.includes('◆ ' + E.horizon.title), pv);
  await p.mouse.move(cb.x + cb.width * 0.5, cb.y - 80);

  // "Comment on this": jumps, note with the title; once saved, the comment follows the card in the list
  await p.click('.agent-note[data-note="sunset"] .note-foot button');
  await settled(p);
  v = await videoState(p);
  const note = await p.evaluate(() => ({ value: document.querySelector('#note').value, focus: document.activeElement.id }));
  ok('"Comment on this" jumps and fills the note', Math.abs(v.t - (E.sunset.t + 0.02)) < 0.03 && note.value === `On “${E.sunset.title}”: ` && note.focus === 'note',
    `t=${v.t.toFixed(3)} ${JSON.stringify(note)}`);
  await p.keyboard.type('fits');
  await p.keyboard.press('Enter');
  await waitUntil(p, () => document.querySelector('#note').value === '');
  await waitUntil(p, () => document.querySelectorAll('#entries > li.entry').length === 1);
  const order = await p.evaluate(() => [...document.querySelectorAll('#entries > li')].map((li) => li.dataset.note || 'comment'));
  const iSunset = order.indexOf('sunset'), iComment = order.indexOf('comment');
  ok('Own comment follows the card, count (1)', iComment === iSunset + 1 && (await text(p, '#count')) === '(1)', order.slice(iSunset - 1, iSunset + 3).join(','));
  const mine = await p.evaluate(() => fetch('api/entries?cut=cut6').then((r) => r.json()));
  for (const e of mine.entries) await p.evaluate((id) => fetch(`api/entries/${id}?person=Reviewer%20A`, { method: 'DELETE' }), e.id);

  // filter: "Claude" shows Claude's comments, a status filter hides them (they have no status)
  await waitUntil(p, () => document.querySelectorAll('#entries > li.entry').length === 0);
  await p.selectOption('#filter-person', 'Claude');
  ok('Filter "Claude": all changes', (await p.$$('#entries > li.agent-note')).length === nNotes);
  await p.selectOption('#filter-person', '');
  await p.selectOption('#filter-status', 'open');
  ok('Status filter: changes hidden, the hint fits', (await p.$$('#entries > li.agent-note')).length === 0 && (await text(p, '#list-empty')).startsWith('Nothing'),
    await text(p, '#list-empty'));
  await p.selectOption('#filter-status', '');

  // ---------------------------------------------------------------- desktop, cut5: replies under the comments
  const add = (b) => p.evaluate((b) => fetch('api/entries', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) }).then((r) => r.json()), b);
  const a1 = (await add({ cut: 'cut5', t: 84.31, text: 'What is the cut in this sentence for? It sounds cut off', person: 'Reviewer B' })).entry;
  const a2 = (await add({ cut: 'cut5', t: 289.5, action: 'remove', person: 'Reviewer B' })).entry;
  const d1 = (await add({ cut: 'cut5', t: 451.44, text: 'Suggestion: put this as text at the end', person: 'Reviewer A' })).entry;
  const REPLY1 = 'The half sentence "and then we could go there again some day, that would be nice" is out in cut6. In the source there are '
    + 'more than five seconds of silence between "that" and "would be nice"; with the cut inside it, the sentence sounded cut off. '
    + 'The trip is not mentioned anywhere else in the film.';
  for (const [id, reply] of [[a1.id, REPLY1], [a2.id, 'Removed in cut6.']]) {
    await p.evaluate(([id, reply]) => fetch('api/status', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: [id], status: 'done', reply }) }), [id, reply]);
  }
  await p.selectOption('#cut-select', 'cut5');
  await waitUntil(p, () => document.title.includes('cut5'));
  await waitUntil(p, () => document.querySelectorAll('#entries > li.entry').length === 3);
  const tl5 = await timeline(p, 'cut5');
  const n5 = tl5.decisions.entries.filter((d) => d.t != null).length;
  const answered = await p.evaluate(() => [...document.querySelectorAll('.entry.answered')].map((li) => ({
    id: +li.dataset.id, label: li.querySelector('.agent-reply .agent-label').textContent, text: li.querySelector('.agent-reply p').textContent,
    dimContent: +getComputedStyle(li.querySelector('.content')).opacity, dimReply: +getComputedStyle(li.querySelector('.agent-reply')).opacity,
    dimLi: +getComputedStyle(li).opacity })));
  ok('cut5: two comments with a reply from Claude', answered.length === 2 && answered.every((x) => x.label === '↳ Reply from Claude'), JSON.stringify(answered.map((x) => x.id)));
  const x1 = answered.find((x) => x.id === a1.id) || {};
  ok('cut5: the reply stands in full under the comment', x1.text === REPLY1, (x1.text || '').slice(0, 60));
  ok('cut5: done entries dimmed, the reply fully readable', Math.abs(x1.dimContent - 0.66) < 0.02 && x1.dimReply === 1 && x1.dimLi === 1, JSON.stringify(x1));
  ok('cut5: comment without a reply has no reply box', !(await p.$(`.entry[data-id="${d1.id}"] .agent-reply`)));
  const leg5 = await text(p, '#legend');
  ok('cut5: legend with changes and replies', leg5.includes(`Change by Claude (${n5})`) && leg5.includes('Reply from Claude (2)'), leg5);
  ok('cut5: violet core at the answered dot', violet(await pixel(p, a1.t, 9)), JSON.stringify(await pixel(p, a1.t, 9)));
  // while watching: the info box shows the comment with its reply
  await seekTo(p, 85.5);
  const now5 = await text(p, '#info-now');
  ok('cut5: info box shows the comment with its reply at the position', now5.includes('Reviewer B · 1:24.3') && now5.includes('↳ Reply from Claude')
    && now5.includes('The half sentence'), now5.slice(0, 200));
  await p.evaluate(() => { document.querySelector('#entries').scrollTop = 0; });
  await p.click('#info-now .now-comment');
  await sleep(900);
  v = await videoState(p);
  ok('cut5: a click shows the comment in the list, the position stays', (await p.getAttribute(`.entry[data-id="${a1.id}"]`, 'class')).includes('selected')
    && await inList(p, `.entry[data-id="${a1.id}"]`) && Math.abs(v.t - 85.5) < 0.05, `t=${v.t.toFixed(2)}`);
  await p.selectOption('#filter-person', 'Claude');
  ok('cut5: filter "Claude" = changes and answered comments', (await p.$$('#entries > li.entry')).length === 2
    && (await p.$$('#entries > li.agent-note')).length === tl5.decisions.entries.length);
  await p.selectOption('#filter-person', '');
  if (process.env.SHOTS) { await seekTo(p, 85.5); await screenshot(p, 'comments_desktop_cut5'); }

  // following: while playing, the comment at the current position comes into the visible part of the list
  await sleep(8500);                                   // after "show in the list" following pauses for 8 s
  await seekTo(p, 447.0);                              // shot of the comment, but before its window in the info box
  await p.evaluate(() => { document.querySelector('#entries').scrollTop = 0; });
  await sleep(300);
  const before = await inList(p, `.entry[data-id="${d1.id}"]`);
  await p.evaluate(() => document.querySelector('#video').play());
  await sleep(2200);
  await p.evaluate(() => document.querySelector('#video').pause());
  ok('The list follows playback', !before && await inList(p, `.entry[data-id="${d1.id}"]`), `visible before=${before}`);
  // not while someone scrolls the list
  await seekTo(p, 449.0);
  await p.evaluate(() => { document.querySelector('#entries').scrollTop = 0; });
  await p.mouse.move(1300, 700); await p.mouse.wheel(0, -50); await sleep(200);
  await p.evaluate(() => { document.querySelector('#entries').scrollTop = 0; });
  await p.evaluate(() => document.querySelector('#video').play());
  await sleep(1500);
  await p.evaluate(() => document.querySelector('#video').pause());
  ok('No following right after scrolling oneself', !(await inList(p, `.entry[data-id="${d1.id}"]`)));
  await ctx.close();

  // ---------------------------------------------------------------- iPhone (touch)
  const ctxM = await browser.newContext({ ...PHONE, httpCredentials: credentials, locale: 'en-US' });
  const m = await ctxM.newPage(); watch(m, errors, 'iPhone: ');
  await m.addInitScript(() => localStorage.setItem('sf_name', 'Reviewer Phone'));
  await m.goto(`${BASE}?f=cut6`, { waitUntil: 'domcontentloaded' });
  await waitUntil(m, () => document.querySelector('#video').readyState >= 1, null, 30000);
  await waitUntil(m, () => document.querySelectorAll('.agent-note').length > 0);
  const width = await m.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: innerWidth }));
  ok('iPhone: nothing sticks out sideways', width.sw <= 390 && width.iw === 390, JSON.stringify(width));
  await seekTo(m, E.pace.t + 1);
  const nowM = await text(m, '#now');
  ok('iPhone: the change stands below the timeline', await visible(m, '#now') && nowM.includes(E.pace.title) && !(await visible(m, '#info-now')), nowM.slice(0, 120));
  const posNow = await m.evaluate(() => ({ tl: document.querySelector('#tl-canvas').getBoundingClientRect().bottom, now: document.querySelector('#now').getBoundingClientRect().top }));
  ok('iPhone: right below the timeline', posNow.now >= posNow.tl && posNow.now - posNow.tl < 20, JSON.stringify(posNow));
  await screenshot(m, 'comments_iphone_now');
  // a tap on the bracket: only the preview, no jump
  await m.evaluate(() => scrollTo(0, 0));
  const c = await m.locator('#tl-canvas').boundingBox();
  let before2 = await videoState(m);
  await m.touchscreen.tap(c.x + c.width * (300 / tl6.duration), c.y + 15); await sleep(500);
  let after = await videoState(m);
  ok('iPhone: a tap on the bracket does not jump, the preview names the change', Math.abs(after.t - before2.t) < 0.01
    && (await text(m, '#tl-preview')).includes('◆ ' + E.horizon.title));
  // a tap on the text of a card: no jump; ▶: jump
  const why = m.locator('.agent-note[data-note="silence"] .note-why');
  await why.scrollIntoViewIfNeeded();
  const whyBox = await why.boundingBox();
  before2 = await videoState(m);
  await m.touchscreen.tap(whyBox.x + 20, whyBox.y + 5); await sleep(400);
  after = await videoState(m);
  ok('iPhone: a tap on the text does not jump', Math.abs(after.t - before2.t) < 0.01, `${before2.t.toFixed(2)} -> ${after.t.toFixed(2)}`);
  const play = m.locator('.agent-note[data-note="map"] .note-time');
  await play.scrollIntoViewIfNeeded();
  const playBox = await play.boundingBox();
  await m.touchscreen.tap(playBox.x + playBox.width / 2, playBox.y + playBox.height / 2);
  await settled(m);
  after = await videoState(m);
  ok('iPhone: ▶ jumps to the card', Math.abs(after.t - (E.map.t + 0.02)) < 0.03, `t=${after.t.toFixed(3)}`);
  if (process.env.SHOTS) { await m.evaluate(() => scrollTo(0, 0)); await screenshot(m, 'comments_iphone_cut6_full', { fullPage: true }); }
  // cut5 on the phone: the comment with its reply below the timeline, a tap shows it in the list
  await m.selectOption('#cut-select', 'cut5');
  await waitUntil(m, () => document.title.includes('cut5'));
  await waitUntil(m, () => document.querySelectorAll('.entry.answered').length === 2);
  await seekTo(m, 85.5);
  await m.evaluate(() => scrollTo(0, 0));
  ok('iPhone cut5: comment with reply below the timeline', (await text(m, '#now')).includes('↳ Reply from Claude'));
  await screenshot(m, 'comments_iphone_cut5_now');
  const nowComment = await m.locator('#now .now-comment').boundingBox();
  before2 = await videoState(m);
  await m.touchscreen.tap(nowComment.x + nowComment.width / 2, nowComment.y + 10); await sleep(1200);
  after = await videoState(m);
  const target = await m.evaluate((id) => { const r = document.querySelector(`.entry[data-id="${id}"]`).getBoundingClientRect(); return { top: r.top, h: innerHeight }; }, a1.id);
  ok('iPhone cut5: a tap shows the comment in the list, no jump', Math.abs(after.t - before2.t) < 0.01 && target.top > 0 && target.top < target.h - 60, JSON.stringify(target));
  await screenshot(m, 'comments_iphone_cut5_list');
  await ctxM.close();

  ok('No errors in the browser', errors.length === 0, errors.join(' | '));
  await browser.close();
  done();
});
