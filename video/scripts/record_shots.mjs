// Record the BrandForge app screen shots (03-05) headlessly with Playwright.
// recordVideo captures the page VIEWPORT ONLY — no browser chrome, no address bar,
// so presigned-URL query strings and the Basic-auth dialog can never appear on camera.
// Basic auth is supplied via httpCredentials (read from env or the project .env; never logged).
//
// Companion to render_cards.mjs. Run from the AI_coding root so the bare `playwright`
// import resolves against its node_modules:
//
//   node workspace/projects/backblaze-genblaze/video/scripts/record_shots.mjs
//
// The app must already be serving locally (project venv):
//   .venv/Scripts/python.exe -m uvicorn app.main:create_app --factory --port 8000
//
// Hold time per shot is derived from the manifest (scenes.yaml `duration`, which is >= the
// narration length), not hardcoded — so pacing stays in sync with the single source of truth.
//
// Env overrides: BRANDFORGE_BASE_URL, BRANDFORGE_CAMPAIGN_ID, BRANDFORGE_USER,
// BRANDFORGE_PASS, FFMPEG, FFPROBE, SHOTS (comma list of ids), RAW_DIR (output dir,
// default video/raw_shots — point elsewhere to test without touching shipped clips),
// CURSOR=1 (overlay a highlight ring + click ripple for cursor-driven demos).
import { chromium } from 'playwright';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import fs from 'node:fs';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const videoDir = path.resolve(scriptDir, '..');            // .../video
const projectRoot = path.resolve(videoDir, '..');          // .../backblaze-genblaze
const rawShots = process.env.RAW_DIR ? path.resolve(process.env.RAW_DIR) : path.join(videoDir, 'raw_shots');
const narrationDir = path.join(videoDir, 'narration');
const tmpDir = path.join(videoDir, 'build', '_rec');
const PAD_SEC = 2;   // record this much beyond the narration length; assemble.py hard-trims with -t
const CURSOR = !!process.env.CURSOR && !['0', 'false', ''].includes(process.env.CURSOR.toLowerCase());

const BASE = (process.env.BRANDFORGE_BASE_URL || 'http://127.0.0.1:8000').replace(/\/+$/, '');
const CAMPAIGN = process.env.BRANDFORGE_CAMPAIGN_ID || 'smoke-set-001';
// Basic-auth creds go out via httpCredentials — never send them over cleartext to a remote host.
if (!/^https:\/\//i.test(BASE) && !/^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:|\/|$)/i.test(BASE)) {
  console.error(`ERROR: refusing to send Basic-auth over cleartext HTTP to a non-loopback target (${BASE}). Use HTTPS.`);
  process.exit(1);
}
const W = 1920, H = 1080;
const FFMPEG = process.env.FFMPEG
  || (fs.existsSync('C:\\ffmpeg\\bin\\ffmpeg.exe') ? 'C:\\ffmpeg\\bin\\ffmpeg.exe' : 'ffmpeg');
const FFPROBE = process.env.FFPROBE || FFMPEG.replace(/ffmpeg(\.exe)?$/i, 'ffprobe$1');

// Narration length (s) for a scene, via ffprobe. Drives per-shot hold so pacing tracks the VO.
function voSeconds(id) {
  const mp3 = path.join(narrationDir, `${id}.mp3`);
  if (!fs.existsSync(mp3)) return null;
  try {
    const out = execFileSync(
      FFPROBE,
      ['-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', mp3],
      { encoding: 'utf8' },
    );
    const s = parseFloat(out.trim());
    return Number.isFinite(s) && s > 0 ? s : null;
  } catch { return null; }
}

// Per-scene metadata from the manifest — the authoritative pacing source. Narration is
// silence-padded to fill the slot, so the slot (>= narration) is what the visual must cover.
// Minimal block parse (no yaml dep): a `- id:` line opens a scene; `duration:`/`speed:` within it.
const scenesPath = path.join(videoDir, 'scenes.yaml');
function sceneMeta() {
  const map = {};
  if (!fs.existsSync(scenesPath)) return map;
  let cur = null;
  for (const line of fs.readFileSync(scenesPath, 'utf8').split(/\r?\n/)) {
    const idm = /^\s*-\s*id:\s*["']?([A-Za-z0-9._-]+)/.exec(line);
    if (idm) { cur = idm[1]; map[cur] = { duration: null, speed: 1.0 }; continue; }
    if (!cur) continue;
    const dm = /^\s*duration:\s*([\d.]+)/.exec(line);
    if (dm) { map[cur].duration = parseFloat(dm[1]); continue; }
    const sm = /^\s*speed:\s*([\d.]+)/.exec(line);
    if (sm) { map[cur].speed = parseFloat(sm[1]); continue; }
  }
  return map;
}
const META = sceneMeta();

// Hold (ms) = ceil(max(manifest slot, narration length) * speed) + PAD. No hardcoded per-shot
// durations. The *speed factor matters: assemble.py compresses the clip with setpts=PTS/speed,
// so the raw capture must be `speed x` longer to still fill the slot. assemble.py hard-trims
// to the exact slot with -t. Falls back to 15s if the scene isn't found in the manifest.
function holdMsFor(id) {
  const meta = META[id];
  if (!meta) console.warn(`  [${id}] not found in scenes.yaml — pacing from narration/default only`);
  const base = Math.max(meta?.duration || 0, voSeconds(id) || 0) || 15;
  const speed = meta?.speed || 1.0;
  return Math.round((Math.ceil(base * speed) + PAD_SEC) * 1000);
}

// Opt-in (CURSOR=1): overlay a highlight ring that follows the mouse + a click ripple.
// recordVideo doesn't capture the OS cursor, so we inject a DOM one (persists across navigations).
async function injectCursor(page) {
  await page.addInitScript(() => {
    const build = () => {
      if (document.getElementById('__demo_cursor')) return;
      const ring = document.createElement('div');
      ring.id = '__demo_cursor';
      ring.style.cssText = 'position:fixed;left:0;top:0;width:28px;height:28px;margin:-14px 0 0 -14px;'
        + 'border:3px solid rgba(255,138,0,.9);border-radius:50%;box-shadow:0 0 12px 2px rgba(255,138,0,.45);'
        + 'pointer-events:none;z-index:2147483647;opacity:0;transition:opacity .15s';
      document.body.appendChild(ring);
      let shown = false;
      addEventListener('mousemove', (e) => {
        ring.style.left = e.clientX + 'px'; ring.style.top = e.clientY + 'px';
        if (!shown) { ring.style.opacity = '1'; shown = true; }
      }, { passive: true });
      addEventListener('mousedown', (e) => {
        const r = document.createElement('div');
        r.style.cssText = 'position:fixed;left:' + e.clientX + 'px;top:' + e.clientY + 'px;width:12px;height:12px;'
          + 'margin:-6px 0 0 -6px;border-radius:50%;background:rgba(255,138,0,.5);pointer-events:none;'
          + 'z-index:2147483647;animation:__demo_ripple .5s ease-out forwards';
        document.body.appendChild(r); setTimeout(() => r.remove(), 520);
      }, { passive: true });
    };
    const st = document.createElement('style');
    st.textContent = '@keyframes __demo_ripple{to{transform:scale(6);opacity:0}}';
    (document.head || document.documentElement).appendChild(st);
    if (document.body) build(); else addEventListener('DOMContentLoaded', build);
  });
}

// --- credentials: env first, then parse project .env; the values are never printed ---
function loadCreds() {
  let u = process.env.BRANDFORGE_USER;
  let p = process.env.BRANDFORGE_PASS;
  if (!u || !p) {
    const envPath = path.join(projectRoot, '.env');
    if (fs.existsSync(envPath)) {
      for (const line of fs.readFileSync(envPath, 'utf8').split(/\r?\n/)) {
        const m = /^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$/.exec(line);
        if (!m) continue;
        const val = m[2].trim().replace(/^["']|["']$/g, '');
        if (m[1] === 'BRANDFORGE_USER' && !u) u = val;
        if (m[1] === 'BRANDFORGE_PASS' && !p) p = val;
      }
    }
  }
  if (!u || !p) {
    console.error('ERROR: BRANDFORGE_USER / BRANDFORGE_PASS not found (env or project .env).');
    process.exit(1);
  }
  return { username: u, password: p };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Wait until the gallery <img> cards have actually decoded, so recorded frames aren't blank.
async function waitForGallery(page) {
  try {
    await page.waitForLoadState('networkidle', { timeout: 20000 });
  } catch { /* keep going; some presigned imgs may still be trickling in */ }
  try {
    await page.waitForFunction(() => {
      const imgs = Array.from(document.querySelectorAll('img.card__img'));
      return imgs.length === 0 || imgs.every((i) => i.complete && i.naturalWidth > 0);
    }, { timeout: 20000 });
  } catch {
    console.warn('  WARNING: gallery images did not all settle before timeout — verify the clip is not showing broken cards');
  }
}

// Smoothly scroll the page top->bottom->top over ~totalMs to reveal the whole set.
async function panSet(page, totalMs) {
  const height = await page.evaluate(() => document.body.scrollHeight);
  const viewport = H;
  const maxScroll = Math.max(0, height - viewport);
  const half = totalMs / 2;
  const steps = 60;
  for (let i = 0; i <= steps; i++) {
    await page.evaluate((y) => window.scrollTo({ top: y, behavior: 'auto' }), (maxScroll * i) / steps);
    await sleep(half / steps);
  }
  for (let i = steps; i >= 0; i--) {
    await page.evaluate((y) => window.scrollTo({ top: y, behavior: 'auto' }), (maxScroll * i) / steps);
    await sleep(half / steps);
  }
}

// Each handler receives (page, hold) where `hold` (ms) is derived from the scene's narration
// length — no hardcoded durations. assemble.py hard-trims to the scripted slot with -t.
const SHOTS = {
  '03-live-gallery': async (page, hold) => {
    await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
    await waitForGallery(page);
    await sleep(hold);
  },
  '04-campaign-set': async (page, hold) => {
    await page.goto(`${BASE}/?campaign_id=${encodeURIComponent(CAMPAIGN)}`, { waitUntil: 'domcontentloaded' });
    await waitForGallery(page);
    await sleep(1200);
    await panSet(page, Math.max(3000, hold - 2400));  // pan spans the narration
    await sleep(1200);
  },
  '05-replay': async (page, hold) => {
    // Start on the full gallery, then "replay" a past campaign by id -> same set, fresh URLs.
    const first = Math.round(hold * 0.3);
    await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
    await waitForGallery(page);
    await sleep(first);
    await page.goto(`${BASE}/?campaign_id=${encodeURIComponent(CAMPAIGN)}`, { waitUntil: 'domcontentloaded' });
    await waitForGallery(page);
    await sleep(hold - first);
  },
};

async function recordShot(browser, creds, id, run, hold) {
  const shotTmp = path.join(tmpDir, id);
  fs.rmSync(shotTmp, { recursive: true, force: true });
  fs.mkdirSync(shotTmp, { recursive: true });

  const context = await browser.newContext({
    viewport: { width: W, height: H },
    deviceScaleFactor: 1,
    httpCredentials: creds,
    recordVideo: { dir: shotTmp, size: { width: W, height: H } },
  });
  const page = await context.newPage();
  if (CURSOR) await injectCursor(page);
  const video = page.video();
  try {
    await run(page, hold);
  } finally {
    await context.close(); // finalize the .webm and free the context even if run() throws
  }
  const webm = await video.path(); // only reached on success — a failed shot writes no clip

  const out = path.join(rawShots, `${id}.mp4`);
  execFileSync(FFMPEG, [
    '-y', '-i', webm,
    '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
    '-an', out,
  ], { stdio: 'inherit' });
  const mb = (fs.statSync(out).size / (1024 * 1024)).toFixed(2);
  console.log(`  -> ${path.relative(projectRoot, out)} (${mb} MB)`);
}

const creds = loadCreds();
fs.mkdirSync(rawShots, { recursive: true });
fs.mkdirSync(tmpDir, { recursive: true });

const only = (process.env.SHOTS || '').split(',').map((s) => s.trim()).filter(Boolean);
const ids = Object.keys(SHOTS).filter((id) => only.length === 0 || only.includes(id));

console.log(`recording ${ids.length} shot(s) against ${BASE} (campaign ${CAMPAIGN})`
  + `${CURSOR ? ' [cursor overlay]' : ''} -> ${path.relative(projectRoot, rawShots)}`);
const browser = await chromium.launch();
const failed = [];
try {
  for (const id of ids) {
    const hold = holdMsFor(id);
    const m = META[id];
    console.log(`[${id}] recording… (hold ${(hold / 1000).toFixed(0)}s; `
      + `manifest=${m?.duration ?? '—'}s speed=${m?.speed ?? 1} vo=${(voSeconds(id) ?? 0).toFixed(0)}s)`);
    try {
      await recordShot(browser, creds, id, SHOTS[id], hold);   // shots are independent — isolate failures
    } catch (e) {
      failed.push(id);
      console.error(`[${id}] FAILED (skipped, other shots continue): ${e.message}`);
    }
  }
} finally {
  await browser.close();
}
if (failed.length) {
  console.error(`done with ${failed.length} failure(s): ${failed.join(', ')} — re-run with SHOTS=${failed.join(',')}`);
  process.exitCode = 1;
} else {
  console.log('done.');
}
