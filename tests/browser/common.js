'use strict';
// Shared helpers of the browser tests: Chrome via playwright-core, login, check output, waiting, video and timeline.
// Environment: BASE (http://127.0.0.1:PORT/), PWFILE (file with the password), USER_NAME (login, default "tester"),
// CHROME (default /usr/bin/google-chrome), SHOTS (optional folder for screenshots).
const fs = require('fs');
const os = require('os');
const path = require('path');
const { chromium, devices } = require('playwright-core');

const BASE = process.env.BASE;
if (!BASE || !process.env.PWFILE) {
  console.error(`Usage: BASE=http://127.0.0.1:8295/ PWFILE=/path/to/password.txt node ${path.basename(process.argv[1])}`);
  process.exit(2);
}
const SHOTS = process.env.SHOTS ? path.resolve(process.env.SHOTS) : '';
const credentials = { username: process.env.USER_NAME || 'tester', password: fs.readFileSync(process.env.PWFILE, 'utf8').trim() };
const IPHONE_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1';
const PHONE = { viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true, userAgent: IPHONE_UA };

// Chrome puts its singleton socket at $TMPDIR/.com.google.Chrome.XXXXXX/SingletonSocket, and a Unix socket path must fit
// into 108 bytes, otherwise Chrome aborts at start (SIGTRAP). With a long TMPDIR, Chrome and its profile get a private
// folder inside TMPDIR; Chrome runs in a subfolder of it with the relative TMPDIR "..". Removed when the test ends.
let browserEnv = null;
function chromeEnv() {
  if (browserEnv) return browserEnv;
  const tmp = os.tmpdir();
  if (path.join(tmp, '.com.google.Chrome.XXXXXX', 'SingletonSocket').length < 100) return (browserEnv = process.env);
  const own = fs.mkdtempSync(path.join(tmp, 'chrome-'));
  fs.mkdirSync(path.join(own, 'cwd'));
  process.env.TMPDIR = own;                       // Playwright creates the profile in os.tmpdir()
  process.chdir(path.join(own, 'cwd'));           // the browser inherits the working directory
  process.on('exit', () => { try { process.chdir(tmp); fs.rmSync(own, { recursive: true, force: true }); } catch (e) { /* best effort */ } });
  return (browserEnv = { ...process.env, TMPDIR: '..' });
}

function launch(args = ['--autoplay-policy=no-user-gesture-required']) {
  return chromium.launch({ executablePath: process.env.CHROME || '/usr/bin/google-chrome', headless: true, args, env: chromeEnv() });
}

// "OK    name" / "FAIL  name" per check; done() prints the summary and exits with 1 if a check failed
function checks() {
  const results = [];
  const ok = (name, condition, info = '') => {
    results.push(!!condition);
    console.log(`${condition ? 'OK    ' : 'FAIL  '}${name}${info ? '  ' + info : ''}`);
  };
  const done = () => {
    const failed = results.filter((x) => !x).length;
    console.log(`\n${results.length - failed}/${results.length} checks passed`);
    process.exit(failed ? 1 : 0);
  };
  return { ok, done, results };
}
// an exception ends the test with exit code 2 (the checks so far are printed)
function main(fn) {
  fn().catch((e) => { console.log(`ABORT ${(e && e.stack) || e}`); process.exit(2); });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// own polling instead of waitForFunction: its polling needs eval, which the CSP of the app forbids
async function waitUntil(page, fn, arg = null, timeout = 15000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await page.evaluate(fn, arg)) return true;
    await sleep(100);
  }
  throw new Error(`Timeout waiting for ${fn.toString().slice(0, 90)}`);
}
// collects page errors and console errors (optionally ignoring some console texts)
function watch(page, errors, prefix = '', ignore = []) {
  page.on('pageerror', (e) => errors.push(prefix + String(e)));
  page.on('console', (m) => { if (m.type() === 'error' && !ignore.some((x) => m.text().includes(x))) errors.push(prefix + m.text()); });
}
const videoState = (p) => p.evaluate(() => {
  const v = document.querySelector('#video');
  return { t: v.currentTime, d: v.duration, rs: v.readyState, paused: v.paused, seeking: v.seeking };
});
async function seekTo(p, t) {
  await p.evaluate((x) => { document.querySelector('#video').currentTime = x; }, t);
  await waitUntil(p, () => !document.querySelector('#video').seeking);
  await sleep(200);
}
const visible = (p, sel) => p.evaluate((s) => {
  const e = document.querySelector(s);
  return !!e && !e.closest('[hidden]') && e.getClientRects().length > 0 && getComputedStyle(e).display !== 'none';
}, sel);
const text = (p, sel) => p.evaluate((s) => { const e = document.querySelector(s); return e ? e.textContent : ''; }, sel);
// colour of the timeline canvas at time t and height y (CSS pixels)
const pixel = (p, t, y) => p.evaluate(([t, y]) => {
  const cv = document.querySelector('#tl-canvas'), r = cv.getBoundingClientRect(), dpr = cv.width / r.width;
  const duration = Math.min(document.querySelector('#video').duration || 1e9, 1e9);
  const x = Math.round(t / duration * r.width * dpr), d = cv.getContext('2d').getImageData(x, Math.round(y * dpr), 1, 1).data;
  return [d[0], d[1], d[2], d[3]];
}, [t, y]);
// the agent's violet (#c4b5fd) at full strength
const violet = (c) => c[3] > 200 && c[2] > 200 && c[0] > 150 && c[1] > 140 && c[0] < 225;
const timeline = (p, cut) => p.evaluate((f) => fetch(`media/${f}/timeline.json`).then((r) => r.json()), cut);
async function screenshot(p, name, opts = {}) {
  if (SHOTS) await p.screenshot({ path: path.join(SHOTS, `${name}.png`), ...opts });
}

module.exports = {
  BASE, SHOTS, credentials, PHONE, IPHONE_UA, devices, launch, checks, main, sleep, waitUntil, watch, videoState, seekTo,
  visible, text, pixel, violet, timeline, screenshot,
};
