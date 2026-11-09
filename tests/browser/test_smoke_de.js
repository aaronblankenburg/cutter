'use strict';
// German smoke run: the review app with "ui_language": "de" in project.json (the browser itself is set to English).
// The page loads in German, labels, messages and the agent's comments are German, a comment can be posted and shows
// German texts, ?lang=en still switches to English, and no title, aria-label or button text is left untranslated.
// Run only against the synthetic test data with an empty comment database and ui_language "de" (run.py does both).
// BASE=http://127.0.0.1:8295/ PWFILE=... [SHOTS=dir] node test_smoke_de.js
const { BASE, credentials, PHONE, launch, checks, main, sleep, waitUntil, watch, seekTo, visible, text, timeline, screenshot } = require('./common');

const { ok, done } = checks();
const texts = (p, sel) => p.$$eval(sel, (els) => els.map((e) => e.textContent.replace(/\s+/g, ' ').trim()));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
// a time as the German interface writes it (decimal comma)
const short = (x) => { const z = Math.round(x * 10) / 10, m = Math.floor(z / 60); return `${m}:${(z - m * 60).toFixed(1).padStart(4, '0').replace('.', ',')}`; };
// texts of the user interface by place in the page: title, aria-label and placeholder of every element and the own
// text of controls and labels (not the content: comments, notes, names, timeline data)
const uiTexts = (p) => p.evaluate(() => {
  const CONTROLS = 'button, label, h2, h3, th, td, dt, option, [data-i18n], .agent-label, .note-badge, .leg, .refs-label, '
    + '.agent-note p > b, #position, #range, #list-empty, .entry .status, .entry .action, .entry .when small, #info-kind, .tl-text .tip';
  const place = (e) => {
    const parts = [];
    for (let x = e; x && x !== document.body; x = x.parentElement) {
      parts.unshift(`${x.tagName.toLowerCase()}${x.id ? '#' + x.id : ''}:${[...x.parentElement.children].indexOf(x)}`);
    }
    return parts.join('>');
  };
  const out = {};
  for (const e of document.body.querySelectorAll('*')) {
    if (e.closest('#toast')) continue;
    const k = place(e);
    for (const a of ['title', 'aria-label', 'placeholder']) if (e.getAttribute(a)) out[`${k} @${a}`] = e.getAttribute(a);
    if (e.matches(CONTROLS)) {
      const own = [...e.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join('').replace(/\s+/g, ' ').trim();
      if (own) out[`${k} @text`] = own;
    }
  }
  return out;
});

main(async () => {
  const browser = await launch();
  const errors = [];

  // ---------------------------------------------------------------- first visit, cut4 (no notes of the agent)
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, httpCredentials: credentials, locale: 'en-US' });
  const p = await ctx.newPage(); watch(p, errors);
  await p.goto(`${BASE}?f=cut4`, { waitUntil: 'domcontentloaded' });
  await p.waitForSelector('#name-dialog[open]', { timeout: 30000 });
  const dialog = { title: await text(p, '#name-dialog h2'), go: await text(p, '#name-form button[type=submit]'), names: await texts(p, '#name-suggestions button') };
  ok('de: name dialog in German', dialog.title === 'Wie heißt du?' && dialog.go === 'Los geht’s' && same(dialog.names, ['Reviewer A', 'Reviewer B']), JSON.stringify(dialog));
  await p.click('#name-suggestions button:first-child');
  await waitUntil(p, () => !document.querySelector('#name-dialog').open);
  await waitUntil(p, () => document.querySelector('#video').readyState >= 1, null, 30000);
  const tl4 = await timeline(p, 'cut4');
  const labels = await p.evaluate(() => ({
    lang: document.documentElement.lang, title: document.title, cut: document.querySelector('.cut-choice span').textContent,
    comments: document.querySelector('.list-title span').textContent, status: [...document.querySelectorAll('#filter-status option')].map((o) => o.textContent),
    people: document.querySelector('#filter-person option').textContent, loop: document.querySelector('#loop-button').textContent,
    zoom: document.querySelector('#zoom-button').textContent, placeholder: document.querySelector('#note').placeholder,
    save: document.querySelector('#note-form .save').textContent, info: [...document.querySelectorAll('.info dt')].map((x) => x.textContent),
    io: [...document.querySelectorAll('.io button')].slice(0, 2).map((b) => b.textContent), name: document.querySelector('#name-button').textContent,
  }));
  ok('de: page language and labels in German', labels.lang === 'de' && labels.title === 'Feedback cut4 · Cut 4 (first assembly)' && labels.cut === 'Fassung'
    && labels.comments === 'Anmerkungen' && same(labels.status, ['alle Status', 'offen', 'erledigt', 'verworfen']) && labels.people === 'alle Personen'
    && labels.loop === '⟲ Schnitt' && labels.zoom === 'Lupe' && labels.placeholder === 'Notiz zur aktuellen Stelle … (N)' && labels.save === 'Speichern'
    && same(labels.info, ['Clip', 'Musik', 'Schnitt']) && same(labels.io, ['I Anfang', 'O Ende']) && labels.name === 'Reviewer A', JSON.stringify(labels));
  const quick = await p.$$eval('#quick button', (bs) => bs.map((b) => b.firstChild.textContent));
  ok('de: quick buttons in German', same(quick, ['Raus', 'Kürzer', 'Länger', 'Schnitt früher', 'Schnitt später', 'Anderes Bild', 'Musik leiser',
    'Musik lauter', 'Unverständlich', 'Gefällt mir', 'Frage']), quick.join(', '));
  const speeds = await texts(p, '[data-rate]');
  ok('de: times and speeds with a decimal comma', same(speeds, ['0,5×', '1×', '1,5×', '2×']) && (await text(p, '#position')) === 'Anmerkung zu 0:00,0 · Nr. 1',
    `${speeds.join(' ')} | ${await text(p, '#position')}`);
  ok('de: empty list in German', await visible(p, '#list-empty')
    && (await text(p, '#list-empty')) === 'Noch keine Anmerkungen. Einfach beim Anschauen auf einen Knopf drücken oder eine Notiz schreiben.');

  // a jump via the timeline
  const box = await p.locator('#tl-canvas').boundingBox();
  await p.mouse.click(box.x + box.width * 0.7, box.y + 45);
  await sleep(300);
  const jump = await text(p, '#toast-text');
  ok('de: jump message with "Zurück"', /^Sprung von 0:00,0 nach \d:\d\d,\d$/.test(jump) && (await text(p, '#toast-button')) === 'Zurück' && await visible(p, '#toast-button'), jump);
  await p.click('#toast-button');

  // info box in a shot with dialogue, subtitles and a cutaway (cut4: 130-150, cutaway 136-140)
  await seekTo(p, 137.0);
  const shot = tl4.shots.find((s) => s.start <= 137 && 137 < s.end);
  const info = await p.evaluate(() => ({ kind: document.querySelector('#info-kind').textContent, clip: document.querySelector('#info-clip').textContent,
    edit: document.querySelector('#info-edit').textContent, sentence: document.querySelector('#info-sentence').textContent }));
  const cw = shot.cutaways[0];
  ok('de: info box in German', shot.kind === 'dialogue' && info.kind === 'O-Ton' && info.clip.includes(` · ab ${short(shot.source_in)}`)
    && info.clip.includes(`Einschnitt: ${cw.clip} ab ${short(cw.source_in)} – `)
    && info.edit === `Schnitt Nr. ${shot.nr} → Einschnitt bei 2:16,0` && info.sentence.includes('Untertitel: '), JSON.stringify(info));

  // post comments
  await p.click('#quick button[data-action=shorter]');
  await waitUntil(p, () => document.querySelectorAll('.entry').length === 1);
  const saved = { text: await text(p, '#toast-text'), button: await text(p, '#toast-button'), entry: (await texts(p, '.entry'))[0], count: await text(p, '#count') };
  ok('de: a comment is posted, message and list in German', saved.text === `Gespeichert: Kürzer bei 2:17,0 (Nr. ${shot.nr})` && saved.button === 'Rückgängig'
    && saved.entry.includes('Kürzer') && saved.entry.startsWith('2:17,0') && saved.count === '(1)', JSON.stringify(saved));
  await p.keyboard.press('n');
  await p.keyboard.type('test note');
  await p.keyboard.press('Enter');
  await waitUntil(p, () => document.querySelectorAll('.entry').length === 2);
  const savedNote = await text(p, '#toast-text');
  await p.click('#toast-button');
  await waitUntil(p, () => document.querySelectorAll('.entry').length === 1);
  ok('de: a note is saved and undone in German', savedNote.startsWith('Gespeichert: ') && savedNote.includes('test note') && savedNote.endsWith(`bei 2:17,0 (Nr. ${shot.nr})`)
    && (await text(p, '#toast-text')) === 'Rückgängig gemacht', `${savedNote} | ${await text(p, '#toast-text')}`);
  await seekTo(p, 140.0);
  await p.keyboard.press('i');
  await seekTo(p, 143.5);
  await p.keyboard.press('o');
  const range = await text(p, '#range');
  await p.keyboard.press('x');
  await waitUntil(p, () => document.querySelectorAll('.entry').length === 2);
  const removed = (await texts(p, '.entry'))[1];
  ok('de: range and "Raus" in German', range === 'Bereich 2:20,0–2:23,5' && removed.includes('Raus') && removed.includes('bis 2:23,5'), `${range} | ${removed}`);

  // status and reply from the agent
  const first = (await p.evaluate(() => fetch('api/entries?cut=cut4').then((r) => r.json()))).entries.find((e) => e.action === 'shorter');
  await p.evaluate((id) => fetch('api/status', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids: [id], status: 'done', reply: 'Shortened by two seconds in cut5.' }) }), first.id);
  await waitUntil(p, () => [...document.querySelectorAll('.entry')].some((e) => e.textContent.includes('✓ erledigt')), null, 8000);
  const answered = { label: await text(p, '.entry.answered .agent-label'), legend: await text(p, '#legend') };
  ok('de: status and reply in German', answered.label === '↳ Antwort von Claude' && answered.legend.includes('Anmerkung (Farbe der Person)')
    && answered.legend.includes('Antwort von Claude (1)'), JSON.stringify(answered));
  await p.selectOption('#filter-status', 'rejected');
  const filtered = await text(p, '#list-empty');
  await p.selectOption('#filter-status', '');
  ok('de: filter hint in German', filtered === 'Nichts für diesen Filter.', filtered);

  // dialogs
  await p.click('.entry .tools button[title="Ändern"]');
  await p.waitForSelector('#edit-dialog[open]');
  const edit = await p.evaluate(() => ({
    title: document.querySelector('#edit-dialog h2').textContent, labels: [...document.querySelectorAll('#edit-form label span')].map((s) => s.textContent),
    buttons: [...document.querySelectorAll('#edit-form .buttons button')].map((b) => b.textContent),
    actions: [...document.querySelectorAll('#edit-action option')].slice(0, 2).map((o) => o.textContent) }));
  ok('de: edit dialog in German', edit.title === 'Anmerkung ändern' && same(edit.labels, ['Aktion', 'Reaktion', 'Text', 'auf die aktuelle Videoposition setzen'])
    && same(edit.buttons, ['Löschen', 'Abbrechen', 'Speichern']) && same(edit.actions, ['– keine –', 'Raus']), JSON.stringify(edit));
  await p.click('#edit-form button[value=cancel]');
  await waitUntil(p, () => !document.querySelector('#edit-dialog').open);
  await p.keyboard.press('?');
  await p.waitForSelector('#help-dialog[open]');
  const help = await p.evaluate(() => ({ title: document.querySelector('#help-dialog h2').textContent, key: document.querySelector('#help-dialog th').textContent,
    h3: [...document.querySelectorAll('#help-dialog h3')].map((h) => h.textContent) }));
  ok('de: help in German', help.title === 'Bedienung' && help.key === 'Leertaste' && same(help.h3, ['Auf dem Handy', 'Anmerkungen', 'Kommentare von Claude (violett)']),
    JSON.stringify(help));
  await screenshot(p, 'smoke_de_help');
  await p.keyboard.press('Escape');

  // ---------------------------------------------------------------- cut6: the agent's changes
  await p.selectOption('#cut-select', 'cut6');
  await waitUntil(p, () => document.title.includes('cut6'));
  await waitUntil(p, () => document.querySelectorAll('.agent-note').length > 0);
  const tl6 = await timeline(p, 'cut6');
  const E = Object.fromEntries(tl6.decisions.entries.map((d) => [d.id, d]));
  const cards = await p.evaluate(() => {
    const t = (s) => [...document.querySelectorAll(s)].map((e) => e.textContent);
    return { labels: [...new Set(t('.agent-note .agent-label'))], time: t('.agent-note[data-note="map"] .note-time')[0], badge: t('.agent-note .note-badge')[0],
      button: [...new Set(t('.agent-note .note-foot button'))], bold: [...new Set(t('.agent-note p > b'))], refs: t('.refs-label')[0],
      where: t('.agent-note[data-note="pace"] .note-refs .where')[0], legend: document.querySelector('#legend').textContent };
  });
  ok('de: the agent\'s changes in German', same(cards.labels, ['◆ Claude · allgemein', '◆ Änderung von Claude']) && cards.time === '▶ 0:23,1–0:30,0'
    && cards.badge === 'Frage an euch' && same(cards.button, ['Anmerkung dazu']) && same(cards.bold.sort(), ['Frage an euch:', 'Warum:'])
    && cards.refs === 'Geht zurück auf:' && cards.where === ' cut5 bei 1:24,3: ' && cards.legend.includes('Änderung von Claude (6)'), JSON.stringify(cards));
  await p.click('.agent-note[data-note="pace"] .note-time');
  await seekTo(p, E.pace.t + 0.02);
  const jumpNote = await text(p, '#toast-text');
  ok('de: info box and jump message for a change in German', jumpNote === 'Sprung von 0:00,0 nach 1:19,9' && (await text(p, '#info-now')).includes('◆ Änderung von Claude seit cut5'),
    `${jumpNote} | ${(await text(p, '#info-now')).slice(0, 80)}`);
  await p.click('.agent-note[data-note="sunset"] .note-foot button');
  await sleep(300);
  const prefix = await p.inputValue('#note');
  await p.keyboard.type('fits');
  await p.keyboard.press('Enter');
  await waitUntil(p, () => document.querySelectorAll('.entry').length === 1);
  const posted = (await p.evaluate(() => fetch('api/entries?cut=cut6').then((r) => r.json()))).entries[0];
  ok('de: "Anmerkung dazu" starts the note with the title in German quotes', prefix === `Zu „${E.sunset.title}“: ` && posted.text === `Zu „${E.sunset.title}“: fits`,
    `${prefix} | ${posted.text}`);
  await screenshot(p, 'smoke_de_cut6');

  // the same state in English (?lang=en beats the project language): nothing of the interface may be equal in both
  // languages, apart from strings that are the same in both tables of i18n.js and the content (names, titles)
  const pe = await ctx.newPage(); watch(pe, errors);
  await pe.goto(`${BASE}?f=cut6&lang=en`, { waitUntil: 'domcontentloaded' });
  await waitUntil(pe, () => document.querySelector('#video').readyState >= 1 && document.querySelectorAll('.entry').length === 1, null, 30000);
  const english = { lang: await pe.evaluate(() => document.documentElement.lang), comments: await text(pe, '.list-title span'), cut: await text(pe, '.cut-choice span') };
  ok('?lang=en overrides the project language', english.lang === 'en' && english.comments === 'Comments' && english.cut === 'Cut', JSON.stringify(english));
  for (const q of [p, pe]) { await seekTo(q, E.pace.t + 1); await q.evaluate(() => { document.querySelector('#entries').scrollTop = 0; }); }
  await sleep(500);
  const de = await uiTexts(p), en = await uiTexts(pe);
  const allowed = new Set(await p.evaluate(() => {
    const T = window.REVIEW_STRINGS, out = Object.keys(T.en).filter((k) => T.de[k] === T.en[k]).map((k) => T.en[k]);
    return out.concat([...document.querySelectorAll('#cut-select option, #filter-person option')].map((o) => o.textContent),
      document.querySelector('#film-title').textContent, document.querySelector('#name-button').textContent, 'Claude', 'Esc');
  }));
  const shared = Object.keys(de).filter((k) => k in en && de[k] === en[k] && /[A-Za-zÄÖÜäöüß]{3}/.test(de[k]) && !allowed.has(de[k]));
  ok('de: no title, aria-label or label of the interface left in English', Object.keys(de).length > 150 && shared.length === 0,
    `${Object.keys(de).length} texts compared; equal in both languages: ${shared.slice(0, 6).map((k) => `${k.split('>').pop()} = "${de[k]}"`).join('; ')}`);
  const tables = await p.evaluate(() => {
    const T = window.REVIEW_STRINGS, vars = (s) => (s.match(/\{\w+\}/g) || []).sort().join(',');
    const en = Object.keys(T.en), de = Object.keys(T.de);
    return { onlyEn: en.filter((k) => !(k in T.de)), onlyDe: de.filter((k) => !(k in T.en)), placeholders: en.filter((k) => k in T.de && vars(T.en[k]) !== vars(T.de[k])) };
  });
  ok('de: both string tables have the same keys and placeholders', !tables.onlyEn.length && !tables.onlyDe.length && !tables.placeholders.length, JSON.stringify(tables));
  await pe.close();
  await ctx.close();

  // ---------------------------------------------------------------- phone
  const ctxM = await browser.newContext({ ...PHONE, httpCredentials: credentials, locale: 'en-US' });
  const m = await ctxM.newPage(); watch(m, errors, 'phone: ');
  await m.addInitScript(() => localStorage.setItem('sf_name', 'Reviewer Phone'));
  await m.goto(`${BASE}?f=cut6`, { waitUntil: 'domcontentloaded' });
  await waitUntil(m, () => document.querySelector('#video').readyState >= 1, null, 30000);
  await m.locator('#tl-canvas').scrollIntoViewIfNeeded();
  const cb = await m.locator('#tl-canvas').boundingBox();
  await m.touchscreen.tap(cb.x + cb.width * 0.6, cb.y + 40); await sleep(400);
  const bubble = (await text(m, '#tl-text')).replace(/\s+/g, ' ');
  await seekTo(m, E.pace.t + 1);
  const nowM = await text(m, '#now');
  ok('de: phone: timeline hint and "now" below the timeline in German', bubble.includes('Zum Springen waagrecht ziehen') && nowM.includes('◆ Änderung von Claude seit cut5'),
    `${bubble} | ${nowM.slice(0, 60)}`);
  await screenshot(m, 'smoke_de_phone', { fullPage: true });
  await ctxM.close();

  // ---------------------------------------------------------------- a new cut since the last visit
  const ctxN = await browser.newContext({ viewport: { width: 1280, height: 800 }, httpCredentials: credentials, locale: 'en-US' });
  await ctxN.addInitScript(() => {              // the state of an earlier visit: cut4 open, cut5 and cut6 not seen yet
    if (!localStorage.getItem('sf_cut')) { localStorage.setItem('sf_name', 'Reviewer B'); localStorage.setItem('sf_cut', 'cut4'); localStorage.setItem('sf_cuts_seen', 'cut4'); }
  });
  const n = await ctxN.newPage(); watch(n, errors);
  await n.goto(BASE, { waitUntil: 'domcontentloaded' });
  let toast = null;
  for (let i = 0; i < 60 && !(toast && toast.visible); i++) {
    toast = await n.evaluate(() => ({ visible: !document.querySelector('#toast').hidden, text: document.querySelector('#toast-text').textContent,
      button: document.querySelector('#toast-button').textContent }));
    await sleep(100);
  }
  ok('de: message about a new cut in German', toast.visible && toast.text === 'Neue Fassung cut6 geöffnet' && toast.button === 'Zurück zu cut4', JSON.stringify(toast));
  await ctxN.close();

  ok('No errors in the browser', errors.length === 0, errors.join(' | '));
  await browser.close();
  done();
});
