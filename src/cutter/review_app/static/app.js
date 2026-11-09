'use strict';
/* cutter review app – frontend without dependencies. Project title, reviewers, the agent's name and the UI language
   come from data/project.json (via /api/cuts → app); the browser language is the fallback for the language.
   Older German-keyed timeline.json files are read transparently (normalizeTimeline). */
(() => {
  const $ = (s) => document.querySelector(s);
  const video = $('#video');
  const cv = $('#tl-canvas');
  const ctx = cv.getContext('2d');

  const SECTION_COLORS = ['#5b8def', '#e0719c', '#f0a43c', '#3fbf9f', '#a78bfa', '#f87171',
    '#34d399', '#e8c547', '#60a5fa', '#f472b6', '#2dd4bf', '#c084fc'];
  let PERSON_COLORS = {};          // name (lower case) -> colour, from the project configuration
  const FALLBACK_COLORS = ['#a3e635', '#f472b6', '#c084fc', '#fb7185', '#2dd4bf', '#facc15'];
  const GROUPS = {
    remove: 'cut', shorter: 'cut', longer: 'cut', cut_earlier: 'cut', cut_later: 'cut',
    other_shot: 'picture', music_softer: 'sound', music_louder: 'sound', unclear: 'sound', like: 'good', question: 'question',
  };
  let NAME_SUGGESTIONS = [];       // suggestions in the name dialog, from the project configuration
  const AGENT_COLOR = '#c4b5fd';   // the agent: changes since the previous cut and replies, like --agent in style.css
  let AGENT = 'Claude';            // author of the change notes (decisions in timeline.json) and of the replies (field "reply")
  let PROJECT_TITLE = '';          // cut off at the front in the cut selector
  let LANG = 'en';

  const store = {
    get(k, def = null) { try { const v = localStorage.getItem('sf_' + k); return v === null ? def : v; } catch (e) { return def; } },
    set(k, v) { try { localStorage.setItem('sf_' + k, v); } catch (e) { /* private window */ } },
  };

  // ------------------------------------------------------------ language
  function t(key, vals = {}) {
    const table = (window.REVIEW_STRINGS || {})[LANG] || {}, base = (window.REVIEW_STRINGS || {}).en || {};
    const s = key in table ? table[key] : (key in base ? base[key] : key);
    return s.replace(/\{(\w+)\}/g, (m, k) => (k in vals ? String(vals[k]) : m));
  }
  function chooseLanguage(configured) {
    const want = (configured || store.get('lang') || navigator.language || 'en').slice(0, 2).toLowerCase();
    LANG = (window.REVIEW_STRINGS || {})[want] ? want : 'en';
    document.documentElement.lang = LANG;
  }
  function translatePage() {
    for (const e of document.querySelectorAll('[data-i18n]')) e.textContent = t(e.dataset.i18n);
    for (const e of document.querySelectorAll('[data-i18n-html]')) e.innerHTML = t(e.dataset.i18nHtml);   // static strings only
    for (const e of document.querySelectorAll('[data-i18n-title]')) e.title = t(e.dataset.i18nTitle);
    for (const e of document.querySelectorAll('[data-i18n-aria]')) e.setAttribute('aria-label', t(e.dataset.i18nAria));
    for (const e of document.querySelectorAll('[data-i18n-placeholder]')) e.placeholder = t(e.dataset.i18nPlaceholder);
    for (const b of document.querySelectorAll('[data-rate]')) b.textContent = `${String(b.dataset.rate).replace('.', t('decimal'))}×`;
    for (const e of document.querySelectorAll('.agent-name')) e.textContent = AGENT;
    document.title = t('app_title');
  }
  const kindText = (k) => (k ? t('kind_' + k) : '');
  const actionText = (k) => (k ? t('act_' + k) : '');
  const statusText = (s) => t('status_' + s);

  const S = {
    name: store.get('name', ''), login: '', cuts: [], actions: [], reactions: [],
    f: null, tl: null, fps: 25, shots: [], editPoints: [], sections: [], music: [], thumbs: [],
    sectionColor: new Map(), entries: [], rev: null, firstLoad: true, polling: false,
    filterPerson: store.get('filterPerson', ''), filterStatus: store.get('filterStatus', ''),
    rIn: null, rOut: null, loop: null, zoom: false, reverse: null,
    hover: null, drag: null, selected: null, infoKey: '', lastNr: null, timeText: '', positionText: '',
    notesData: null, notes: [], noteSelected: null, followPause: 0, nowKey: '',
  };

  // ------------------------------------------------------------ helpers
  const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
  const duration = () => {
    const v = isFinite(video.duration) && video.duration > 0 ? video.duration : Infinity;
    const d = Math.min(v, S.tl ? S.tl.duration : Infinity);
    return isFinite(d) ? d : 0;
  };
  const frameNr = (x) => Math.floor(x * S.fps + 1e-3);
  const r3 = (x) => Math.round(x * 1000) / 1000;

  function tc(x) {
    const f = frameNr(Math.max(0, x || 0)), s = Math.floor(f / S.fps);
    const p = (n) => String(n).padStart(2, '0');
    return `${p(Math.floor(s / 60))}:${p(s % 60)}:${p(f % S.fps)}`;
  }
  function short(x) {
    if (x == null) return '–';
    const z = Math.round(Math.max(0, x) * 10) / 10, m = Math.floor(z / 60);
    return `${m}:${(z - m * 60).toFixed(1).padStart(4, '0').replace('.', t('decimal'))}`;
  }
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function button(text, title, fn) {
    const b = el('button', null, text);
    b.type = 'button'; b.title = title; b.setAttribute('aria-label', title);
    b.addEventListener('click', (ev) => { ev.stopPropagation(); b.blur(); fn(); });
    return b;
  }
  const regexText = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  function shortTitle(x) { return PROJECT_TITLE ? (x || '').replace(new RegExp('^' + regexText(PROJECT_TITLE) + '\\s*[–-]\\s*'), '') : (x || ''); }
  function applyAppSettings(a) {
    a = a || {};
    if (a.title) PROJECT_TITLE = String(a.title);
    if (a.agent) AGENT = String(a.agent);
    const people = Array.isArray(a.people) ? a.people.filter((p) => p && p.name) : [];
    NAME_SUGGESTIONS = people.map((p) => String(p.name));
    PERSON_COLORS = Object.fromEntries(people.filter((p) => p.color).map((p) => [String(p.name).toLowerCase(), String(p.color)]));
    chooseLanguage(new URLSearchParams(location.search).get('lang') || a.ui_language);
    translatePage();
  }
  function personColor(n) {
    const k = (n || '').toLowerCase();
    if (PERSON_COLORS[k]) return PERSON_COLORS[k];
    let h = 0;
    for (const c of k) h = (h * 31 + c.codePointAt(0)) >>> 0;
    return FALLBACK_COLORS[h % FALLBACK_COLORS.length];
  }
  const own = (e) => S.name && e.person.toLowerCase() === S.name.toLowerCase();

  async function api(method, url, body) {
    const opt = { method, cache: 'no-store', headers: {} };
    if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    const res = await fetch(url, opt);
    let d = null;
    try { d = await res.json(); } catch (e) { /* no JSON */ }
    if (!res.ok) throw new Error((d && d.error) || `Error ${res.status}`);
    return d;
  }

  // ------------------------------------------------------------ older timeline.json (German keys)
  function normalizeTimeline(tl) {
    if (!tl || !tl.einstellungen) return tl;
    const kinds = { dialog: 'dialogue', bild: 'picture', atmo: 'ambience', titel: 'title', schwarz: 'black' };
    const span = (u) => ({ start: u.start, end: u.ende, text: u.text });
    const d = tl.entscheidungen;
    return {
      version: tl.fassung, title: tl.titel, fps: tl.fps, duration: tl.dauer, poster: tl.poster,
      sections: (tl.abschnitte || []).map((a) => ({ name: a.name, start: a.start, end: a.ende })),
      shots: tl.einstellungen.map((s) => ({
        nr: s.nr, start: s.start, end: s.ende, section: s.abschnitt, kind: kinds[s.art] || s.art, clip: s.clip, file: s.datei,
        source_in: s.quelle_in, note: s.notiz, text: s.text, sentences: (s.saetze || []).map(span), subtitles: (s.untertitel || []).map(span),
        cutaways: (s.einschnitte || []).map((c) => ({ start: c.start, end: c.ende, clip: c.clip, source_in: c.quelle_in, note: c.notiz })),
      })),
      edit_points: (tl.schnitte || []).map((c) => ({ t: c.t, before: c.vor, after: c.nach,
        kind: { einstellung: 'shot', einschnitt_an: 'cutaway_in', einschnitt_aus: 'cutaway_out' }[c.art] || c.art })),
      music: (tl.musik || []).map((m) => ({ cue: m.cue, title: m.titel, artist: m.kuenstler, start: m.start, end: m.ende })),
      video: tl.video ? { file: tl.video.datei } : null,
      thumbnails: tl.vorschau ? { vtt: tl.vorschau.vtt } : null,
      decisions: d ? { compared_to: d.vergleich, entries: (d.eintraege || []).map((e) => ({ id: e.id, title: e.titel, t: e.t, t_to: e.t_bis,
        text: e.text, why: e.warum, question: e.frage,
        refs: (e.bezug || []).map((r) => ({ version: r.fassung, id: r.id, person: r.person, t: r.t, text: r.text })) })) } : null,
    };
  }

  // ------------------------------------------------------------ timeline queries
  function shotIndex(x) {
    const a = S.shots; let lo = 0, hi = a.length - 1, r = 0;
    while (lo <= hi) { const m = (lo + hi) >> 1; if (a[m].start <= x + 1e-6) { r = m; lo = m + 1; } else hi = m - 1; }
    return r;
  }
  const shotAt = (x) => (S.shots.length ? S.shots[shotIndex(x)] : null);
  const musicAt = (x) => S.music.find((m) => m.start <= x && x < m.end) || null;
  const notesAt = (x) => S.notes.filter((d) => d.t != null && d.t - 1e-3 <= x && x < (d.t_to != null ? d.t_to : d.t + 2));
  // comments "at this point" while watching: their range, otherwise 4 s from the comment
  const commentsAt = (x) => S.entries.filter((e) => e.t - 0.3 <= x && x < (e.t_to != null ? e.t_to : e.t + 4));
  const truncate = (s, n) => (s.length > n ? s.slice(0, n - 1).replace(/\s+\S*$/, '') + ' …' : s);
  function nearestEdit(x) {
    let best = null;
    for (const c of S.editPoints) if (!best || Math.abs(c.t - x) < Math.abs(best.t - x)) best = c;
    return best;
  }
  const currentEdit = () => S.loop || nearestEdit(video.currentTime);
  function editText(c) {
    if (!c) return '–';
    if (c.kind === 'cutaway_in') return t('edit_cut_in', { a: c.before });
    if (c.kind === 'cutaway_out') return t('edit_cut_out', { b: c.after });
    return t('edit_shot', { a: c.before, b: c.after });
  }
  function thumbAt(x) {
    const a = S.thumbs; let lo = 0, hi = a.length - 1, r = -1;
    while (lo <= hi) { const m = (lo + hi) >> 1; if (a[m].start <= x) { r = m; lo = m + 1; } else hi = m - 1; }
    return r >= 0 ? a[r] : null;
  }
  function parseVtt(txt, base) {
    const time = (s) => { const p = s.trim().split(':').map(Number); return p.reduce((acc, x) => acc * 60 + x, 0); };
    const out = [];
    for (const block of txt.replace(/\r/g, '').split(/\n\n+/)) {
      const z = block.trim().split('\n'), i = z.findIndex((l) => l.includes('-->'));
      if (i < 0 || !z[i + 1]) continue;
      const [a, b] = z[i].split('-->').map((s) => time(s.trim().split(/\s+/)[0]));
      const m = z[i + 1].trim().match(/^(.*?)#xywh=(\d+),(\d+),(\d+),(\d+)$/);
      if (m) out.push({ start: a, end: b, url: new URL(m[1], base).href, x: +m[2], y: +m[3], w: +m[4], h: +m[5] });
    }
    return out.sort((p, q) => p.start - q.start);
  }

  // ------------------------------------------------------------ loading
  async function start() {
    chooseLanguage(new URLSearchParams(location.search).get('lang'));
    translatePage();
    controls();
    try {
      const r = await api('GET', 'api/cuts');
      Object.assign(S, { login: r.login, cuts: r.cuts, actions: r.actions, reactions: r.reactions });
      applyAppSettings(r.app);
    } catch (e) { $('#loading-hint').textContent = t('server_unreachable', { msg: e.message }); return; }
    quickButtons();
    const sel = $('#cut-select');
    sel.textContent = '';
    for (const f of S.cuts) {
      const o = el('option', null, `${f.id}${f.title ? ' – ' + shortTitle(f.title) : ''}`);
      o.value = f.id; sel.append(o);
    }
    if (!S.cuts.length) { $('#loading-hint').textContent = t('no_cuts'); return; }
    const url = new URLSearchParams(location.search).get('f');
    const newest = S.cuts[S.cuts.length - 1].id;
    const remembered = store.get('cut') || store.get('fassung');           // older key from the German version
    const known = String(store.get('cuts_seen', '') || store.get('fassungen_gesehen', '') || '').split(',').filter(Boolean);
    // If a new cut appeared since the last visit, the app opens it instead of the remembered one,
    // unless the link names a cut explicitly (?f=…). Before, people stayed on the old one without noticing.
    const isNew = !url && !!remembered && remembered !== newest && !known.includes(newest) && S.cuts.some((f) => f.id === remembered);
    const choice = isNew ? newest : ([url, remembered].find((x) => x && S.cuts.some((f) => f.id === x)) || newest);
    store.set('cuts_seen', S.cuts.map((f) => f.id).join(','));
    sel.value = choice;
    showName();
    await loadCut(choice);
    if (isNew) {
      message(t('new_cut_opened', { cut: newest }), { label: t('back_to', { cut: remembered }),
        action: () => { sel.value = remembered; loadCut(remembered); } });
    }
    if (!S.name) await askName();
    setInterval(() => { if (!document.hidden) fetchEntries(); }, 2500);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) fetchEntries(); });
  }

  async function loadCut(id) {
    S.f = id; store.set('cut', id);
    const p = new URLSearchParams(location.search); p.set('f', id);
    history.replaceState(null, '', '?' + p.toString() + location.hash);
    stopLoop(); stopReverse(); S.rIn = S.rOut = null;
    let tl;
    try {
      const res = await fetch(`media/${encodeURIComponent(id)}/timeline.json`, { cache: 'no-cache' });
      if (!res.ok) throw new Error(`Error ${res.status}`);
      tl = normalizeTimeline(await res.json());
    } catch (e) { $('#loading-hint').textContent = t('timeline_missing', { msg: e.message }); return; }
    Object.assign(S, { tl, fps: tl.fps || 25, shots: tl.shots || [], editPoints: tl.edit_points || [],
      sections: tl.sections || [], music: tl.music || [], thumbs: [], infoKey: '', lastNr: null });
    S.sectionColor = new Map(S.sections.map((a, i) => [a.name, SECTION_COLORS[i % SECTION_COLORS.length]]));
    // the agent's change notes of this cut (from decisions.json); they appear in the list between the comments
    const dec = tl.decisions && Array.isArray(tl.decisions.entries) ? tl.decisions : null;
    Object.assign(S, { notesData: dec, notes: dec ? dec.entries : [], noteSelected: null, nowKey: '' });
    $('#entries').scrollTop = 0;
    $('#film-title').textContent = tl.title || t('app_title');
    document.title = t('page_title', { cut: id, title: shortTitle(tl.title) });
    const hint = $('#loading-hint'); hint.hidden = false; hint.textContent = t('loading');
    if (tl.poster) video.poster = `media/${encodeURIComponent(id)}/${tl.poster}`;
    video.src = `media/${encodeURIComponent(id)}/${(tl.video && tl.video.file) || 'review.mp4'}`;
    if (tl.thumbnails && tl.thumbnails.vtt) {
      const vttUrl = new URL(`media/${encodeURIComponent(id)}/${tl.thumbnails.vtt}`, location.href);
      fetch(vttUrl, { cache: 'no-cache' }).then((r) => r.text()).then((x) => { if (S.f === id) S.thumbs = parseVtt(x, vttUrl); })
        .catch(() => {});
    }
    S.rev = null; S.firstLoad = true; S.entries = [];
    personFilter(); list();
    showRange();
    await fetchEntries();
    info(); time(); draw();
  }

  async function fetchEntries() {
    if (!S.f || S.polling) return;
    S.polling = true;
    const f = S.f;
    try {
      const r = await api('GET', `api/entries?cut=${encodeURIComponent(f)}&rev=${encodeURIComponent(S.rev || '')}`);
      connection(true);
      if (f !== S.f || r.unchanged) return;
      if (!S.firstLoad) news(S.entries, r.entries);
      S.firstLoad = false; S.entries = r.entries; S.rev = r.rev;
      personFilter(); list(); info(); draw();
    } catch (e) {
      connection(false);
    } finally { S.polling = false; }
  }

  function news(old, fresh) {
    const before = new Map(old.map((e) => [e.id, e]));
    const others = fresh.filter((e) => !before.has(e.id) && !own(e));
    const status = fresh.filter((e) => before.has(e.id) && before.get(e.id).status !== e.status && e.status !== 'open');
    if (others.length === 1) message(`${others[0].person}: ${t('at', { what: describe(others[0]), t: short(others[0].t), nr: others[0].nr })}`);
    else if (others.length > 1) message(t('new_comments', { n: others.length }));
    else if (status.length) message(t(status.length === 1 ? 'status_changed_one' : 'status_changed', { n: status.length, status: statusText(status[0].status) }));
  }
  function describe(e) {
    return [e.action ? actionText(e.action) : '', e.reaction || '', e.text ? t('quoted', { text: e.text }) : ''].filter(Boolean).join(' ');
  }
  function connection(ok) {
    const v = $('#connection');
    v.classList.toggle('gone', !ok);
    v.title = ok ? t('connected') : t('disconnected');
  }

  // ------------------------------------------------------------ playback
  function play() { stopReverse(); const p = video.play(); if (p && p.catch) p.catch(() => {}); }
  function toggle() { if (S.reverse) { stopReverse(); return; } if (video.paused) play(); else video.pause(); }
  function seek(x, opt = {}) {
    x = clamp(x, 0, Math.max(0, duration() - 0.5 / S.fps));
    if (S.loop && !opt.loop && (x < S.loop.t - 2.2 || x > S.loop.t + 2.2)) stopLoop();
    video.currentTime = x;
    time(); info(); draw();
    return x;
  }
  // make larger jumps through the timeline or the list visible and reversible
  function reportJump(from, to) {
    if (from == null || to == null || Math.abs(to - from) < 5) return;
    message(t('jump', { from: short(from), to: short(to) }), { label: t('back'), action: () => seek(from) });
  }
  function relative(d) { stopReverse(); seek(video.currentTime + d); }
  function frame(d) {
    video.pause(); stopReverse();
    const n = clamp(frameNr(video.currentTime) + d, 0, Math.max(0, Math.floor(duration() * S.fps) - 1));
    seek((n + 0.5) / S.fps);
  }
  function nextShot() {
    const i = shotIndex(video.currentTime + 1e-3);
    if (i + 1 < S.shots.length) seek(S.shots[i + 1].start + 0.5 / S.fps);
  }
  function prevShot() {
    const x = video.currentTime, i = shotIndex(x), s = S.shots[i];
    if (!s) return;
    if (x - s.start > 0.6) seek(s.start + 0.5 / S.fps);
    else if (i > 0) seek(S.shots[i - 1].start + 0.5 / S.fps);
  }
  function setSpeed(r) { video.playbackRate = r; speedButtons(); }
  function speedButtons() {
    document.querySelectorAll('[data-rate]').forEach((b) => b.classList.toggle('on', Math.abs(parseFloat(b.dataset.rate) - video.playbackRate) < 0.01));
    time();
  }
  // J/K/L
  function forwards() {
    stopReverse();
    if (video.paused) { setSpeed(1); play(); return; }
    const st = [1, 1.5, 2], i = st.indexOf(video.playbackRate);
    setSpeed(st[Math.min(st.length - 1, i + 1)] || 1);
  }
  function stop() { stopReverse(); video.pause(); }
  function backwards() {
    video.pause();
    if (S.reverse) S.reverse.speed = Math.min(8, S.reverse.speed * 2);
    else { S.reverse = { speed: 1, last: performance.now() }; reverseStep(); }
    time();
  }
  function reverseStep() {
    if (!S.reverse) return;
    const now = performance.now();
    const dt = clamp((now - S.reverse.last) / 1000, 1 / S.fps, 0.3);
    S.reverse.last = now;
    const x = video.currentTime - dt * S.reverse.speed;
    if (x <= 0) { video.currentTime = 0; stopReverse(); return; }
    video.currentTime = x;
  }
  function stopReverse() { if (S.reverse) { S.reverse = null; time(); } }

  // edit preview
  function loop() {
    if (S.loop) { stopLoop(); return; }
    const c = nearestEdit(video.currentTime);
    if (!c) return;
    S.loop = c;
    $('#loop-button').classList.add('on');
    hint(t('edit_preview', { edit: editText(c) }));
    seek(Math.max(0, c.t - 2), { loop: true });
    play();
  }
  function checkLoop() {
    const c = S.loop;
    if (c && !video.paused && video.currentTime >= c.t + 2) video.currentTime = Math.max(0, c.t - 2);
  }
  function stopLoop() {
    if (!S.loop) return;
    S.loop = null;
    $('#loop-button').classList.remove('on');
    hint(null); info(); draw();
  }
  function hint(text) { const h = $('#video-hint'); h.hidden = !text; h.textContent = text || ''; }

  function toggleZoom() { S.zoom = !S.zoom; $('#zoom-button').classList.toggle('on', S.zoom); draw(); }
  function fullscreen() {
    const r = $('#video-frame');
    if (document.fullscreenElement || document.webkitFullscreenElement) {
      (document.exitFullscreen || document.webkitExitFullscreen).call(document);
    } else if (r.requestFullscreen) {
      r.requestFullscreen().catch(() => { if (video.webkitEnterFullscreen) video.webkitEnterFullscreen(); });
    } else if (r.webkitRequestFullscreen) r.webkitRequestFullscreen();
    else if (video.webkitEnterFullscreen) video.webkitEnterFullscreen();
  }

  // range I/O
  function range() {
    if (S.rIn == null) return null;
    const a = S.rIn, b = S.rOut;
    return { t: b != null ? Math.min(a, b) : a, t_to: b != null && Math.abs(b - a) >= 1 / S.fps ? Math.max(a, b) : null };
  }
  function setIn() { const x = video.currentTime; S.rIn = x; if (S.rOut != null && S.rOut <= x) S.rOut = null; showRange(); }
  function setOut() {
    const x = video.currentTime;
    if (S.rIn == null || S.rIn >= x) { const s = shotAt(x); S.rIn = s && s.start < x - 0.04 ? s.start : Math.max(0, x - 2); }
    S.rOut = x; showRange();
  }
  function clearRange() { S.rIn = S.rOut = null; showRange(); }
  function showRange() {
    const b = range(), e = $('#range');
    e.hidden = !b; $('#range-clear').hidden = !b;
    if (b) e.textContent = b.t_to != null ? t('range_full', { a: short(b.t), b: short(b.t_to) }) : t('range_open', { a: short(b.t) });
    position(); draw();
  }

  // ------------------------------------------------------------ comments
  async function create({ action = null, reaction = null, text = null } = {}) {
    if (!S.name && !(await askName())) return;
    const field = $('#note');
    const note = text != null ? text : field.value.trim();
    if (!action && !reaction && !note) { field.focus(); return; }
    const b = range(), x = b ? b.t : video.currentTime;
    const body = { cut: S.f, t: r3(x), t_to: b && b.t_to != null ? r3(b.t_to) : null, action, reaction, text: note, person: S.name };
    if (action === 'cut_earlier' || action === 'cut_later') { const c = currentEdit(); if (c) body.edit_t = c.t; }
    try {
      const r = await api('POST', 'api/entries', body);
      field.value = ''; S.rIn = S.rOut = null; showRange();
      S.entries.push(r.entry); S.entries.sort((p, q) => p.t - q.t || p.id - q.id);
      personFilter(); list(); draw();
      message(t('saved', { what: describe(r.entry), t: short(r.entry.t), nr: r.entry.nr }),
        { label: t('undo'), action: () => remove(r.entry, true) });
    } catch (e) { message(t('not_saved', { msg: e.message }), { error: true }); }
  }

  async function remove(e, quiet) {
    if (!quiet && !window.confirm(t('delete_confirm'))) return;
    try {
      await api('DELETE', `api/entries/${e.id}?person=${encodeURIComponent(S.name)}`);
      S.entries = S.entries.filter((x) => x.id !== e.id);
      personFilter(); list(); draw();
      message(quiet ? t('undone') : t('deleted'));
    } catch (err) { message(t('delete_failed', { msg: err.message }), { error: true }); }
  }

  function edit(e) {
    const d = $('#edit-dialog');
    video.pause(); stopReverse();
    $('#edit-position').textContent = `${short(e.t)}${e.t_to != null ? '–' + short(e.t_to) : ''} · Nr. ${e.nr ?? '–'} · ${e.person}`;
    const sa = $('#edit-action'), sr = $('#edit-reaction');
    sa.textContent = ''; sr.textContent = '';
    sa.append(Object.assign(el('option', null, t('none_option')), { value: '' }));
    for (const k of S.actions) sa.append(Object.assign(el('option', null, actionText(k)), { value: k }));
    sr.append(Object.assign(el('option', null, t('none_option')), { value: '' }));
    for (const r of S.reactions) sr.append(Object.assign(el('option', null, r), { value: r }));
    sa.value = e.action || ''; sr.value = e.reaction || ''; $('#edit-text').value = e.text || '';
    $('#edit-here').checked = false;
    $('#edit-delete').onclick = () => { d.close('cancel'); remove(e, false); };
    d.onclose = async () => {
      if (d.returnValue !== 'ok') return;
      const body = { person: S.name, action: sa.value || null, reaction: sr.value || null, text: $('#edit-text').value };
      if ($('#edit-here').checked) { body.t = r3(video.currentTime); body.t_to = null; }
      try {
        const r = await api('PATCH', `api/entries/${e.id}`, body);
        S.entries = S.entries.map((x) => (x.id === e.id ? r.entry : x)).sort((p, q) => p.t - q.t || p.id - q.id);
        list(); draw(); message(t('changed'));
      } catch (err) { message(t('change_failed', { msg: err.message }), { error: true }); }
    };
    d.returnValue = '';
    d.showModal();
  }

  function select(e, from = video.currentTime) {
    S.selected = e.id;
    video.pause(); stopReverse();
    const to = seek(e.t);
    list();
    const li = document.querySelector(`.entry[data-id="${e.id}"]`);
    if (li) li.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    reportJump(from, to);
  }

  // person filter: the reviewers plus the agent (its change notes and the comments it replied to)
  const hasAgent = () => S.notes.length > 0 || S.entries.some((e) => e.reply);
  function personFilter() {
    const sel = $('#filter-person'), names = [...new Set(S.entries.map((e) => e.person))].sort((a, b) => a.localeCompare(b, LANG));
    if (hasAgent() && !names.includes(AGENT)) names.push(AGENT);
    if (S.filterPerson && !names.includes(S.filterPerson)) names.push(S.filterPerson);
    const want = ['', ...names].join('\n') + '|' + LANG;
    if (sel.dataset.names !== want) {
      sel.textContent = '';
      sel.append(Object.assign(el('option', null, t('all_people')), { value: '' }));
      for (const n of names) sel.append(Object.assign(el('option', null, n), { value: n }));
      sel.dataset.names = want;
    }
    sel.value = S.filterPerson;
    $('#filter-status').value = S.filterStatus;
  }
  function commentVisible(e) {
    if (S.filterStatus && e.status !== S.filterStatus) return false;
    if (!S.filterPerson) return true;
    return S.filterPerson === AGENT ? !!e.reply : e.person === S.filterPerson;
  }
  // the agent's change notes have no status: visible with "all statuses" and person "all" or the agent
  const notesVisible = () => !S.filterStatus && (!S.filterPerson || S.filterPerson === AGENT);

  // one list for everything, by time: the reviewers' comments (with the agent's reply below) and the agent's change notes.
  // Colour = who (reviewers in their colour, the agent violet), shape = what (card with a diamond = change, indented arrow = reply).
  function list() {
    const ol = $('#entries'), top = ol.scrollTop;
    ol.textContent = '';
    const nr = (shotAt(video.currentTime) || {}).nr;
    const comments = S.entries.filter(commentVisible), notes = notesVisible() ? S.notes : [];
    const items = [
      ...notes.map((d) => ({ note: d, t: d.t == null ? -1 : d.t })),
      ...comments.map((e) => ({ comment: e, t: e.t })),
    ].sort((p, q) => p.t - q.t || (p.note && !q.note ? -1 : q.note && !p.note ? 1 : 0));
    for (const p of items) ol.append(p.note ? noteEl(p.note) : commentEl(p.comment, nr));
    ol.scrollTop = top;
    const n = S.entries.length;
    $('#count').textContent = n ? (comments.length === n ? `(${n})` : t('count_of', { n: comments.length, total: n })) : '';
    legend();
    const empty = $('#list-empty');
    empty.hidden = items.length > 0;
    if (!items.length) {
      empty.textContent = (S.filterPerson || S.filterStatus) && (S.entries.length || S.notes.length) ? t('list_empty_filter') : t('list_empty');
    }
    markNotes();
  }
  function commentEl(e, nr) {
    const li = el('li', `entry ${e.status}${e.reply ? ' answered' : ''}${e.nr === nr ? ' here' : ''}${S.selected === e.id ? ' selected' : ''}`);
    li.dataset.id = e.id; li.dataset.nr = e.nr;
    li.style.setProperty('--person', personColor(e.person));
    const when = el('div', 'when', short(e.t));
    when.append(el('small', null, `Nr. ${e.nr ?? '–'}${e.nr_to ? '–' + e.nr_to : ''}`));
    if (e.t_to != null) when.append(el('small', null, t('until', { t: short(e.t_to) })));
    const content = el('div', 'content');
    const who = el('div', 'who', e.person);
    if (e.status !== 'open') who.append(el('span', `status ${e.status}`, e.status === 'done' ? t('status_done_mark') : statusText(e.status)));
    content.append(who);
    const line = el('div', 'text-line');
    if (e.action) line.append(el('span', 'action', actionText(e.action)));
    if (e.reaction) line.append(el('span', 'reaction', e.reaction));
    if (e.text) line.append(document.createTextNode(e.text));
    content.append(line);
    if (e.edit_point) content.append(el('span', 'cut-info', t('edit_at', { edit: editText(e.edit_point), t: short(e.edit_point.t) })));
    const tools = el('div', 'tools');
    if (own(e)) tools.append(button('✎', t('change'), () => edit(e)), button('🗑', t('delete'), () => remove(e, false)));
    li.append(when, content, tools);
    if (e.reply) li.append(replyEl(e.reply));
    li.addEventListener('click', (ev) => { if (!ev.target.closest('button')) select(e); });
    return li;
  }
  function replyEl(text, brief = false) {
    const box = el('div', 'agent-reply');
    box.append(el('div', 'agent-label', t('reply_from', { agent: AGENT })), el('p', null, brief ? truncate(text, 180) : text));
    return box;
  }
  function noteEl(d) {
    const compared = (S.notesData && S.notesData.compared_to) || '';
    const li = el('li', `agent-note${d.question ? ' with-question' : ''}`);
    li.dataset.note = d.id;
    const head = el('div', 'note-head');
    head.append(el('span', 'agent-label', d.t == null ? t('agent_general', { agent: AGENT }) : t('change_by', { agent: AGENT })));
    if (d.t != null) {
      const z = button(`▶ ${short(d.t)}${d.t_to != null ? '–' + short(d.t_to) : ''}`, t('jump_to', { t: short(d.t) }), () => selectNote(d));
      z.className = 'note-time';
      head.append(z);
    }
    if (d.question) head.append(el('span', 'note-badge', t('question_badge')));
    li.append(head, el('div', 'note-title', d.title));
    if (d.text) li.append(el('p', 'note-text', d.text));
    const para = (cls, label, text) => { const p = el('p', cls); p.append(el('b', null, label), document.createTextNode(' ' + text)); li.append(p); };
    if (d.why) para('note-why', t('why'), d.why);
    if (d.question) para('note-question', t('question'), d.question);
    if (d.refs && d.refs.length) {
      li.append(el('div', 'refs-label', t('refs')));
      const ul = el('ul', 'note-refs');
      for (const r of d.refs) {
        const x = el('li');
        x.style.setProperty('--person', personColor(r.person));
        x.append(el('b', null, r.person), el('span', 'where', t('ref_where', { cut: r.version || compared, at: r.t != null ? t('ref_at', { t: short(r.t) }) : '' })),
          document.createTextNode(r.text));
        ul.append(x);
      }
      li.append(ul);
    }
    const foot = el('div', 'note-foot');
    foot.append(button(t('comment_on'), t('comment_on_title', { title: d.title }), () => commentOnNote(d)));
    li.append(foot);
    return li;
  }
  // legend above the list, only for what occurs in this cut
  function legend() {
    const box = $('#legend'), nReply = S.entries.filter((e) => e.reply).length, nNote = S.notes.filter((d) => d.t != null).length;
    box.textContent = '';
    box.hidden = !(nReply || S.notes.length);
    if (box.hidden) return;
    const part = (cls, sign, text) => { const s = el('span', 'leg ' + cls); s.append(el('i', null, sign), document.createTextNode(text)); box.append(s); };
    part('comment', '●', t('legend_comment'));
    if (nNote) part('note', '◆', t('legend_change', { agent: AGENT, n: nNote }));
    if (nReply) part('reply', '↳', t('legend_reply', { agent: AGENT, n: nReply }));
  }
  function markNotes(now = notesAt(video.currentTime)) {
    const ids = new Set(now.map((d) => d.id));
    for (const li of $('#entries').querySelectorAll('.agent-note')) {
      li.classList.toggle('here', ids.has(li.dataset.note));
      li.classList.toggle('selected', li.dataset.note === S.noteSelected);
    }
  }
  function selectNote(d, from = video.currentTime) {
    if (d.t == null) return;
    S.noteSelected = d.id;
    video.pause(); stopReverse();
    reportJump(from, seek(d.t + 0.5 / S.fps));
    markNotes(); draw();
  }
  // from the info box: show the note in the list, the position stays; a filter hiding it is cleared
  function showNote(d) {
    S.noteSelected = d.id;
    if (!notesVisible()) clearFilter();
    markNotes(); draw();
    const li = $('#entries').querySelector(`.agent-note[data-note="${CSS.escape(d.id)}"]`);
    if (li) { S.followPause = performance.now() + 8000; li.scrollIntoView({ block: 'start', behavior: 'smooth' }); }
  }
  function showComment(e) {
    S.selected = e.id;
    if (!commentVisible(e)) clearFilter();
    list(); draw();
    const li = $('#entries').querySelector(`.entry[data-id="${e.id}"]`);
    if (li) { S.followPause = performance.now() + 8000; li.scrollIntoView({ block: 'start', behavior: 'smooth' }); }
  }
  function clearFilter() {
    S.filterPerson = ''; S.filterStatus = '';
    store.set('filterPerson', ''); store.set('filterStatus', '');
    personFilter(); list();
  }
  // comment on an agent note: to the place (unless already there), the note starts with the title
  function commentOnNote(d) {
    const from = video.currentTime;
    video.pause(); stopReverse();
    S.noteSelected = d.id;
    if (d.t != null && !notesAt(from).some((x) => x.id === d.id)) reportJump(from, seek(d.t + 0.5 / S.fps));
    markNotes(); draw();
    const field = $('#note');
    const prefix = t('note_prefix', { title: d.title });
    field.value = prefix + field.value.replace(/^(On “[^”]*”|Zu „[^“]*“): /, '');
    field.focus();
    try { field.setSelectionRange(field.value.length, field.value.length); } catch (e) { /* older browsers */ }
  }

  // "now at this point" while watching: the agent's change(s) and the comments with replies.
  // On a computer in the info box, on the phone right below the timeline.
  function showNow(x, notes) {
    const comments = commentsAt(x).filter(commentVisible);
    const key = [S.f, LANG, notes.map((d) => d.id).join(','), comments.map((e) => e.id + ':' + (e.reply || '').length).join(',')].join('|');
    if (key === S.nowKey) return;
    S.nowKey = key;
    for (const box of [$('#info-now'), $('#now')]) {
      box.textContent = '';
      box.hidden = !notes.length && !comments.length;
      for (const d of notes) {
        const b = el('button', 'now-note');
        b.type = 'button'; b.title = t('show_in_list');
        const k = el('span', 'agent-label', t('change_by', { agent: AGENT }) + (S.notesData && S.notesData.compared_to ? t('since', { cut: S.notesData.compared_to }) : ''));
        if (d.question) k.append(el('span', 'note-badge', t('question_short')));
        b.append(k, el('span', 'now-title', d.title));
        if (d.text) b.append(el('span', 'now-text', d.text));
        b.addEventListener('click', () => { b.blur(); showNote(d); });
        box.append(b);
      }
      for (const e of comments.slice(0, 3)) {
        const b = el('button', 'now-comment');
        b.type = 'button'; b.title = t('show_comment');
        b.style.setProperty('--person', personColor(e.person));
        b.append(el('span', 'now-who', `${e.person} · ${short(e.t)}`), el('span', 'now-text', describe(e) || '–'));
        if (e.reply) b.append(replyEl(e.reply, true));
        b.addEventListener('click', () => { b.blur(); showComment(e); });
        box.append(b);
      }
    }
  }
  // on a computer the list follows playback, as long as nobody is scrolling it
  function follow() {
    if (video.paused || performance.now() < S.followPause) return;
    const ol = $('#entries');
    if (ol.scrollHeight <= ol.clientHeight + 4) return;
    if (!/(auto|scroll)/.test(getComputedStyle(ol).overflowY)) return;
    const li = ol.querySelector('.agent-note.here, .entry.here');
    if (!li) return;
    const a = li.getBoundingClientRect(), c = ol.getBoundingClientRect();
    if (a.top >= c.top && a.bottom <= c.bottom) return;
    ol.scrollTo({ top: ol.scrollTop + (a.top - c.top) - 6, behavior: 'smooth' });
  }

  // ------------------------------------------------------------ display
  function time() {
    const x = video.currentTime;
    let s = `${tc(x)} / ${tc(duration())}`;
    if (S.reverse) s += ` · ◀◀ ${S.reverse.speed}×`;
    else if (video.playbackRate !== 1) s += ` · ${String(video.playbackRate).replace('.', t('decimal'))}×`;
    if (s !== S.timeText) { S.timeText = s; $('#time').textContent = s; }
    position();
  }
  function position() {
    const b = range(), s = shotAt(b ? b.t : video.currentTime);
    const txt = b ? t('position_range', { nr: s ? s.nr : '–' }) : t('position_at', { t: short(video.currentTime), nr: s ? s.nr : '–' });
    if (txt !== S.positionText) { S.positionText = txt; $('#position').textContent = txt; }
  }
  function playButton() {
    const b = $('#play-button');
    b.textContent = video.paused ? '▶' : '❚❚';
    b.setAttribute('aria-label', video.paused ? t('play') : t('pause'));
  }

  function info() {
    const x = video.currentTime, s = shotAt(x);
    if (!s) return;
    const sz = (s.sentences || []).find((y) => y.start - 0.2 <= x && x < y.end + 0.4) || null;
    const sub = (s.subtitles || []).find((u) => u.start <= x && x < u.end) || null;
    const m = musicAt(x), cw = (s.cutaways || []).find((y) => y.start <= x && x < y.end) || null, c = currentEdit();
    const notes = notesAt(x);
    showNow(x, notes);
    follow();
    const key = [S.f, LANG, s.nr, sz && sz.start, sub && sub.start, m && m.cue, cw && cw.start, c && c.t, !!S.loop,
      notes.map((d) => d.id).join(',')].join('|');
    if (key === S.infoKey) return;
    S.infoKey = key;
    $('#info-nr').textContent = `Nr. ${s.nr}`;
    $('#info-kind').textContent = kindText(s.kind);
    const sec = $('#info-section');
    sec.textContent = s.section || '';
    sec.style.background = S.sectionColor.get(s.section) || '';
    const clip = $('#info-clip');
    clip.textContent = s.clip === 'TXT' || !s.file ? t('title_black') : t('clip_from', { clip: s.clip, file: s.file, t: short(s.source_in) });
    if (cw) clip.append(el('div', null, t('cutaway', { clip: cw.clip, t: short(cw.source_in), note: cw.note ? ' – ' + cw.note : '' })));
    $('#info-music').textContent = m ? `${m.cue} ${t('quoted', { text: m.title })} – ${m.artist}` : '–';
    $('#info-edit').textContent = c ? t('edit_at', { edit: editText(c), t: short(c.t) }) + (S.loop ? t('preview_running') : '') : '–';
    const sentence = $('#info-sentence');
    sentence.textContent = '';
    const quiet = s.kind !== 'dialogue';
    if (sz) {
      sentence.append(document.createTextNode(t('quoted', { text: sz.text })));
      if (quiet) sentence.append(el('span', 'quiet', t('camera_audio')));
    } else if (s.text) {
      sentence.append(el('span', 'quiet', t('in_this_shot', { text: s.text })));
    } else {
      sentence.append(el('span', 'quiet', t('no_speech')));
    }
    if (sub) sentence.append(el('span', 'sub', t('subtitle', { text: sub.text.replace(/\n/g, ' / ') })));
    $('#info-note').textContent = s.note || '';
    markNotes(notes);
    if (S.lastNr !== s.nr) {
      S.lastNr = s.nr;
      for (const li of $('#entries').querySelectorAll('.entry')) li.classList.toggle('here', li.dataset.nr === String(s.nr));
    }
  }

  // ------------------------------------------------------------ timeline
  function view() {
    if (S.drag && S.drag.view) return S.drag.view;
    const d = duration() || 1;
    if (!S.zoom) return [0, d];
    const win = Math.min(40, d);
    const a = clamp(video.currentTime - win / 2, 0, d - win);
    return [a, a + win];
  }
  const LAYOUT = { yDot: 9, yNote: 14, yShot: 20, hShot: 32 };
  function draw() {
    if (!S.tl) return;
    const r = cv.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    const w = r.width, h = r.height;
    if (!w) return;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const [a, b] = view(), X = (x) => (x - a) / (b - a) * w;
    const { yDot, yNote, yShot, hShot } = LAYOUT, yMus = yShot + hShot + 4, hMus = Math.max(10, h - yMus - 4);
    ctx.textBaseline = 'middle';

    // shots, coloured by section
    S.shots.forEach((s, i) => {
      if (s.end < a || s.start > b) return;
      const x0 = X(s.start), x1 = X(s.end), bw = Math.max(1, x1 - x0 - (x1 - x0 > 3 ? 1 : 0));
      ctx.fillStyle = S.sectionColor.get(s.section) || '#777';
      ctx.globalAlpha = i % 2 ? 0.72 : 1;
      ctx.fillRect(x0, yShot, bw, hShot);
      ctx.globalAlpha = 1;
      if (!s.file) { ctx.fillStyle = 'rgba(0,0,0,.65)'; ctx.fillRect(x0, yShot + 5, bw, hShot - 10); }
      ctx.fillStyle = 'rgba(255,255,255,.7)';
      for (const e of s.cutaways || []) ctx.fillRect(X(e.start), yShot + hShot - 5, Math.max(1, X(e.end) - X(e.start)), 3);
      if (S.zoom && x1 - x0 > 18) {
        ctx.fillStyle = 'rgba(0,0,0,.78)'; ctx.font = '600 11px system-ui, sans-serif';
        ctx.fillText(String(s.nr), Math.max(x0, 0) + 4, yShot + 10);
      }
    });
    // section boundaries and names
    ctx.font = '600 11px system-ui, sans-serif';
    for (const sec of S.sections) {
      if (sec.end < a || sec.start > b) continue;
      const x0 = X(sec.start), x1 = X(sec.end);
      ctx.fillStyle = '#0f1216'; ctx.fillRect(x0 - 1, yShot, 2, hShot);
      const name = sec.name || '', room = Math.min(x1, w) - Math.max(x0, 0) - 8;
      if (!S.zoom && room > 24) {
        let txt = name;
        while (txt.length > 3 && ctx.measureText(txt).width > room) txt = txt.slice(0, -2);
        if (txt !== name) txt = txt.replace(/\s+$/, '') + '…';
        if (ctx.measureText(txt).width <= room) { ctx.fillStyle = 'rgba(0,0,0,.75)'; ctx.fillText(txt, Math.max(x0, 0) + 5, yShot + hShot / 2); }
      }
    }
    // music
    ctx.font = '10px system-ui, sans-serif';
    for (const m of S.music) {
      if (m.end < a || m.start > b) continue;
      const x0 = X(m.start), x1 = X(m.end);
      ctx.fillStyle = 'rgba(150,160,175,.32)'; ctx.fillRect(x0, yMus, x1 - x0, hMus);
      const txt = `${m.cue} ${m.title || ''}`, room = Math.min(x1, w) - Math.max(x0, 0) - 6;
      if (room > 16) {
        ctx.save(); ctx.beginPath(); ctx.rect(Math.max(x0, 0), yMus, room + 3, hMus); ctx.clip();
        ctx.fillStyle = '#d4dae2'; ctx.fillText(txt, Math.max(x0, 0) + 3, yMus + hMus / 2); ctx.restore();
      }
    }
    // zoom: second marks
    if (S.zoom) {
      ctx.fillStyle = 'rgba(255,255,255,.35)'; ctx.font = '10px system-ui, sans-serif';
      for (let x = Math.ceil(a / 5) * 5; x <= b; x += 5) { const px = X(x); ctx.fillRect(px, h - 4, 1, 4); if (x % 10 === 0) ctx.fillText(short(x).replace(/[.,]\d$/, ''), px + 2, h - 8); }
    }
    // range I/O
    const br = range();
    if (br) {
      const x0 = X(br.t), x1 = br.t_to != null ? X(br.t_to) : x0;
      ctx.fillStyle = 'rgba(245,185,66,.22)'; ctx.fillRect(x0, 0, Math.max(2, x1 - x0), h);
      ctx.fillStyle = '#f5b942'; ctx.fillRect(x0 - 1, 0, 2, h);
      if (br.t_to != null) ctx.fillRect(x1 - 1, 0, 2, h);
    }
    // the edit from the info box or the edit preview
    const c = currentEdit();
    if (c && c.t >= a && c.t <= b) {
      const x = X(c.t);
      if (S.loop) { ctx.fillStyle = 'rgba(245,185,66,.2)'; ctx.fillRect(X(c.t - 2), yShot, X(c.t + 2) - X(c.t - 2), hShot); }
      ctx.fillStyle = S.loop ? '#f5b942' : 'rgba(255,255,255,.9)';
      ctx.beginPath(); ctx.moveTo(x - 5, yShot - 5); ctx.lineTo(x + 5, yShot - 5); ctx.lineTo(x, yShot + 1); ctx.closePath(); ctx.fill();
    }
    // decisions as violet brackets between the comment dots and the shots
    const nowNotes = new Set(notesAt(video.currentTime).map((d) => d.id));
    for (const d of S.notes) {
      if (d.t == null) continue;
      const e1 = d.t_to != null ? d.t_to : d.t;
      if (e1 < a || d.t > b) continue;
      let x0 = X(d.t), x1 = Math.max(X(e1), x0 + 3);
      if (x1 - x0 > 8) { x0 += 1; x1 -= 1; }             // gap between adjacent brackets
      const on = nowNotes.has(d.id) || S.noteSelected === d.id;
      ctx.fillStyle = AGENT_COLOR; ctx.globalAlpha = on ? 1 : 0.7;
      ctx.fillRect(x0, yNote, x1 - x0, on ? 3 : 2);
      ctx.fillRect(x0, yNote, 1.5, yShot - yNote);
      ctx.fillRect(x1 - 1.5, yNote, 1.5, yShot - yNote);
      ctx.globalAlpha = 1;
    }
    // comments as dots (ring = done, violet core = reply from the agent)
    for (const e of S.entries) {
      const visible = commentVisible(e);
      if (e.t < a - 1 || e.t > b + 1) continue;
      const x = X(e.t), col = personColor(e.person);
      ctx.globalAlpha = visible ? 1 : 0.25;
      if (e.t_to != null) { ctx.strokeStyle = col; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(x, yDot); ctx.lineTo(X(e.t_to), yDot); ctx.stroke(); }
      ctx.beginPath(); ctx.arc(x, yDot, e.id === S.selected ? 5.5 : 4, 0, Math.PI * 2);
      if (e.status === 'open') { ctx.fillStyle = col; ctx.fill(); } else { ctx.strokeStyle = col; ctx.lineWidth = 1.6; ctx.stroke(); }
      if (e.reply) { ctx.beginPath(); ctx.arc(x, yDot, 1.9, 0, Math.PI * 2); ctx.fillStyle = AGENT_COLOR; ctx.fill(); }
      ctx.globalAlpha = 1;
    }
    // mouse/finger and playhead
    if (S.hover != null) { ctx.fillStyle = 'rgba(255,255,255,.5)'; ctx.fillRect(X(S.hover) - 0.5, 0, 1, h); }
    const xp = X(video.currentTime);
    ctx.fillStyle = '#ffffff'; ctx.fillRect(xp - 1, 0, 2, h);
  }

  function timeAt(ev) {
    const r = cv.getBoundingClientRect(), [a, b] = view();
    return clamp(a + (ev.clientX - r.left) / r.width * (b - a), 0, duration());
  }
  function dotAt(ev) {
    const finger = ev.pointerType && ev.pointerType !== 'mouse';   // fingers are less precise
    const r = cv.getBoundingClientRect(), y = ev.clientY - r.top;
    if (y > LAYOUT.yShot + (finger ? 14 : 2)) return null;
    const [a, b] = view(), x = ev.clientX - r.left;
    let best = null, bd = finger ? 14 : 8;
    for (const e of S.entries) {
      const d = Math.abs((e.t - a) / (b - a) * r.width - x);
      if (d < bd) { bd = d; best = e; }
    }
    return best;
  }
  function preview(ev, tapped = false) {
    const x = timeAt(ev), box = $('#tl-preview'), r = cv.getBoundingClientRect();
    S.hover = x;
    box.hidden = false;
    box.style.left = `${clamp(ev.clientX - r.left, 100, Math.max(100, r.width - 100))}px`;
    const th = thumbAt(x), img = $('#tl-image');
    img.hidden = !th;
    if (th) {
      img.style.backgroundImage = `url("${th.url}")`;
      img.style.backgroundPosition = `-${th.x}px -${th.y}px`;
    }
    const s = shotAt(x), p = dotAt(ev), txt = $('#tl-text');
    txt.textContent = '';
    txt.append(el('b', null, tc(x)), document.createTextNode(s ? ` · Nr. ${s.nr} · ${s.section}` : ''));
    if (p) txt.append(el('span', 'note', `${p.person}: ${describe(p)}`));
    if (p && p.reply) txt.append(el('span', 'tl-reply', t('tl_reply', { agent: AGENT, text: truncate(p.reply, 110) })));
    for (const d of notesAt(x)) txt.append(el('span', 'tl-note', `◆ ${d.title}`));
    if (tapped) txt.append(el('span', 'tip', t('drag_to_jump')));
    draw();
  }
  let previewTimer = null;
  function hidePreview() {
    clearTimeout(previewTimer); previewTimer = null;
    $('#tl-preview').hidden = true; S.hover = null; draw();
  }

  // ------------------------------------------------------------ dialogs and messages
  function showName() { const b = $('#name-button'); b.textContent = S.name || t('name_button'); b.style.color = S.name ? personColor(S.name) : ''; }
  function askName() {
    return new Promise((resolve) => {
      const d = $('#name-dialog'), inp = $('#name-input'), box = $('#name-suggestions');
      box.textContent = '';
      const names = [...new Set([...S.entries.map((e) => e.person), ...NAME_SUGGESTIONS])];
      for (const n of names) {
        const b = el('button', null, n);
        b.type = 'button';
        b.addEventListener('click', () => { inp.value = n; d.close('ok'); });
        box.append(b);
      }
      inp.value = S.name || '';
      d.oncancel = (ev) => { if (!S.name) ev.preventDefault(); };
      d.onclose = () => {
        const v = inp.value.trim().replace(/\s+/g, ' ').slice(0, 40);
        if (d.returnValue === 'ok' && v) { S.name = v; store.set('name', v); showName(); list(); }
        resolve(!!S.name);
      };
      d.returnValue = '';
      d.showModal();
      setTimeout(() => inp.focus(), 50);
    });
  }
  let messageTimer = null;
  function message(text, { label, action, error } = {}) {
    const box = $('#toast'), b = $('#toast-button');
    $('#toast-text').textContent = text;
    box.classList.toggle('error', !!error);
    b.hidden = !label;
    if (label) { b.textContent = label; b.onclick = () => { box.hidden = true; action(); }; }
    box.hidden = false;
    clearTimeout(messageTimer);
    messageTimer = setTimeout(() => { box.hidden = true; }, label ? 5000 : 2800);
  }

  // ------------------------------------------------------------ controls
  function quickButtons() {
    const box = $('#quick');
    box.textContent = '';
    for (const k of S.actions) {
      const label = actionText(k);
      const b = el('button', null, label);
      b.type = 'button'; b.dataset.action = k; b.dataset.group = GROUPS[k] || '';
      b.title = k === 'remove' ? t('remove_key') : label;
      if (k === 'remove') b.append(el('span', 'key', 'X'));
      b.addEventListener('click', () => { b.blur(); create({ action: k }); });
      box.append(b);
    }
    const rb = $('#reactions');
    rb.textContent = '';
    for (const r of S.reactions) {
      const b = el('button', null, r);
      b.type = 'button'; b.title = t('reaction_title', { r });
      b.addEventListener('click', () => { b.blur(); create({ reaction: r }); });
      rb.append(b);
    }
  }

  function controls() {
    const commands = {
      play: toggle, back2: () => relative(-2), fwd2: () => relative(2), frameBack: () => frame(-1), frameFwd: () => frame(1),
      prevShot, nextShot, editLoop: loop, zoom: toggleZoom, fullscreen,
      setIn, setOut, clearRange,
    };
    document.querySelectorAll('[data-cmd]').forEach((b) => b.addEventListener('click', () => { b.blur(); commands[b.dataset.cmd](); }));
    document.querySelectorAll('[data-rate]').forEach((b) => b.addEventListener('click', () => { b.blur(); setSpeed(parseFloat(b.dataset.rate)); }));

    video.addEventListener('loadedmetadata', () => {
      $('#loading-hint').hidden = true;
      const m = location.hash.match(/t=([\d.]+)/);
      if (m) seek(parseFloat(m[1]));
      time(); info(); draw();
    });
    video.addEventListener('error', () => { const l = $('#loading-hint'); l.hidden = false; l.textContent = t('video_failed'); });
    video.addEventListener('play', () => { playButton(); animate(); });
    video.addEventListener('pause', () => {
      playButton();
      // snap to the frame actually shown, so that single-frame steps continue exactly from there
      if (presented != null && !video.seeking && !S.reverse && Math.abs(presented - video.currentTime) < 0.25) {
        const target = (frameNr(presented + 0.25 / S.fps) + 0.5) / S.fps;
        if (Math.abs(target - video.currentTime) > 0.001) video.currentTime = target;
      }
      time(); info(); draw();
    });
    const frameCallback = () => video.requestVideoFrameCallback((now, md) => { presented = md.mediaTime; frameCallback(); });
    if (video.requestVideoFrameCallback) frameCallback();
    video.addEventListener('timeupdate', () => { if (video.paused) { time(); info(); draw(); } });
    video.addEventListener('seeked', () => { time(); info(); draw(); if (S.reverse) requestAnimationFrame(reverseStep); });
    video.addEventListener('ratechange', speedButtons);
    video.addEventListener('ended', () => { if (S.loop) { video.currentTime = Math.max(0, S.loop.t - 2); play(); } });

    // tapping the picture: single = play/pause, double left/right = ±5 s (phone).
    // "click" instead of "pointerup": a tap that only stops the page from scrolling triggers nothing.
    const area = $('#tap-area');
    let last = { t: 0, side: null }, single = null, tapKind = 'mouse';
    area.addEventListener('pointerdown', (ev) => { tapKind = ev.pointerType || 'mouse'; });
    area.addEventListener('click', (ev) => {
      if (tapKind === 'mouse') { toggle(); return; }
      const r = area.getBoundingClientRect(), side = ev.clientX - r.left < r.width / 2 ? 'left' : 'right';
      const now = performance.now();
      if (now - last.t < 320 && last.side === side) {
        clearTimeout(single); single = null;
        relative(side === 'left' ? -5 : 5);
        const a = $(side === 'left' ? '#tap-left' : '#tap-right');
        a.classList.add('show'); setTimeout(() => a.classList.remove('show'), 450);
        last = { t: now, side };
        return;
      }
      last = { t: now, side };
      clearTimeout(single);
      single = setTimeout(() => { single = null; toggle(); }, 320);
    });
    area.addEventListener('dblclick', (ev) => { ev.preventDefault(); fullscreen(); });

    // timeline with the mouse: hovering shows a preview, a click jumps, dragging scrubs.
    // With a finger: tapping only shows the preview and the comment, only a horizontal drag scrubs.
    // Otherwise a tap while scrolling or on a comment dot changes the position without anyone noticing.
    cv.addEventListener('pointerdown', (ev) => {
      if (ev.pointerType === 'mouse' && ev.button !== 0) return;
      clearTimeout(previewTimer); previewTimer = null;
      cv.setPointerCapture(ev.pointerId);
      S.drag = { x0: ev.clientX, moved: false, last: 0, view: view(), finger: ev.pointerType !== 'mouse', from: video.currentTime };
      preview(ev);
    });
    cv.addEventListener('pointermove', (ev) => {
      if (!S.drag && ev.pointerType !== 'mouse') return;
      preview(ev);
      const z = S.drag;
      if (!z) return;
      if (Math.abs(ev.clientX - z.x0) > (z.finger ? 10 : 4)) z.moved = true;
      const now = performance.now();
      if (z.moved && now - z.last > (z.finger ? 150 : 80)) { z.last = now; seek(timeAt(ev)); }
    });
    cv.addEventListener('pointerup', (ev) => {
      const z = S.drag;
      if (!z) return;
      S.drag = null;
      if (z.finger && !z.moved) {            // only tapped: leave the preview, do not jump
        preview(ev, true);
        previewTimer = setTimeout(hidePreview, 3500);
        return;
      }
      const p = !z.moved && dotAt(ev);
      if (p) select(p, z.from); else reportJump(z.from, seek(timeAt(ev)));
      if (z.finger) hidePreview();
    });
    cv.addEventListener('pointercancel', () => {
      const z = S.drag;
      S.drag = null; hidePreview();
      if (z && z.moved) reportJump(z.from, video.currentTime);
    });
    cv.addEventListener('pointerleave', (ev) => { if (ev.pointerType === 'mouse' && !S.drag) hidePreview(); });
    // a tapped preview disappears as soon as something else is tapped
    document.addEventListener('pointerdown', (ev) => { if (previewTimer && ev.target !== cv) hidePreview(); }, true);
    new ResizeObserver(() => draw()).observe(cv);

    // note: pause while typing
    const note = $('#note');
    note.addEventListener('input', () => { if (!video.paused) video.pause(); stopReverse(); });
    $('#note-form').addEventListener('submit', (ev) => {
      ev.preventDefault();
      const v = note.value.trim();
      if (v) { create({ text: v }); note.blur(); }
    });

    // selects give up the focus after a choice, otherwise shortcuts and letters end up in them
    $('#cut-select').addEventListener('change', (ev) => { ev.target.blur(); loadCut(ev.target.value); });
    $('#name-button').addEventListener('click', (ev) => { ev.currentTarget.blur(); askName(); });
    $('#help-button').addEventListener('click', (ev) => { ev.currentTarget.blur(); $('#help-dialog').showModal(); });
    $('#filter-person').addEventListener('change', (ev) => { ev.target.blur(); S.filterPerson = ev.target.value; store.set('filterPerson', S.filterPerson); list(); draw(); });
    $('#filter-status').addEventListener('change', (ev) => { ev.target.blur(); S.filterStatus = ev.target.value; store.set('filterStatus', S.filterStatus); list(); draw(); });
    const entries = $('#entries');
    for (const kind of ['wheel', 'touchstart', 'pointerdown', 'keydown']) {
      entries.addEventListener(kind, () => { S.followPause = performance.now() + 8000; }, { passive: true });
    }

    document.addEventListener('keydown', (ev) => {
      const target = ev.target, typing = target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
      if (typing) { if (ev.key === 'Escape') target.blur(); return; }
      if (document.querySelector('dialog[open]') || ev.ctrlKey || ev.metaKey || ev.altKey || ev.isComposing) return;
      const k = ev.key;
      // capital letters (with Shift) are not shortcuts: that is how a sentence usually starts
      const capital = ev.shiftKey && k.length === 1 && k.toLowerCase() !== k.toUpperCase();
      const fn = capital ? null : {
        ' ': toggle, k: stop, K: stop, l: forwards, L: forwards, j: backwards, J: backwards,
        ',': () => frame(-1), '.': () => frame(1),
        ArrowLeft: () => (ev.shiftKey ? prevShot() : relative(-2)),
        ArrowRight: () => (ev.shiftKey ? nextShot() : relative(2)),
        n: () => { video.pause(); stopReverse(); note.focus(); }, N: () => { video.pause(); stopReverse(); note.focus(); },
        i: setIn, I: setIn, o: setOut, O: setOut, x: () => create({ action: 'remove' }), X: () => create({ action: 'remove' }),
        s: loop, S: loop, z: toggleZoom, Z: toggleZoom, '?': () => $('#help-dialog').showModal(),
        Escape: () => { clearRange(); stopLoop(); stopReverse(); },
      }[k];
      if (!fn) {
        // typing without clicking into the note field still writes there: set the focus now,
        // the character then lands in the field (instead of further keys moving the video as shortcuts).
        if (k.length === 1 && k !== ' ') { video.pause(); stopReverse(); note.focus(); }
        return;
      }
      ev.preventDefault();
      fn();
    });
    window.addEventListener('resize', draw);
  }

  let animating = false;
  let presented = null;   // time of the frame shown last (requestVideoFrameCallback)
  function animate() {
    if (animating) return;
    animating = true;
    const tick = () => {
      if (video.paused) { animating = false; draw(); return; }
      checkLoop(); time(); info(); draw();
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  start();
})();
