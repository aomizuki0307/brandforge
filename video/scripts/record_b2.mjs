// Record shot 06 — the Backblaze B2 console browse view — headlessly with Playwright,
// reusing an authenticated session (storageState) captured from a live login.
// Headless is REQUIRED: it renders a virtual 1920x1080 viewport regardless of the
// physical screen, and recordVideo captures only that viewport (no browser chrome /
// address bar), so the bucket id in the URL and any account id never appear on camera.
//
//   node workspace/projects/backblaze-genblaze/video/scripts/record_b2.mjs
//
// The storageState file is DELETED after the run (it holds live session cookies).
// Env: B2_STATE (storageState json, default C:\tmp\b2_state.json), B2_BUCKET, B2_RUN_DATE,
//      B2_RUN_UUID, FFMPEG.
import { chromium } from 'playwright';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import fs from 'node:fs';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const videoDir = path.resolve(scriptDir, '..');
const projectRoot = path.resolve(videoDir, '..');
const rawShots = path.join(videoDir, 'raw_shots');
const tmpDir = path.join(videoDir, 'build', '_rec', '06-b2-console');

const STATE = process.env.B2_STATE || 'C:\\tmp\\b2_state.json';
const BUCKET = process.env.B2_BUCKET || 'brandforge-media';
// The file list only populates via the select-bucket -> click-bucket path, not a direct ?bucketId URL.
const SELECT = 'https://secure.backblaze.com/b2_browse_files2.htm?bucketAction=select-bucket';
const W = 1920, H = 1080;
const FFMPEG = process.env.FFMPEG
  || (fs.existsSync('C:\\ffmpeg\\bin\\ffmpeg.exe') ? 'C:\\ffmpeg\\bin\\ffmpeg.exe' : 'ffmpeg');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const RUN_DATE = process.env.B2_RUN_DATE || '2026-07-01';
const RUN_UUID = process.env.B2_RUN_UUID || '337441ac-2eab-4be5-931b-bb6e106176dc';
const NAV_TIMEOUT = 45000;   // headless B2 browse hydrates slowly

// Folder/file rows are <span> with exact own-text, wrapped in a JS <a>. exact getByText is reliable.
const row = (page, name) => page.getByText(name, { exact: true }).first();
async function waitRow(page, name) { await row(page, name).waitFor({ state: 'visible', timeout: NAV_TIMEOUT }); }

// Click a row by exact name, then confirm navigation before holding.
//  - expectNext: wait for that known child row to appear (deep-link confirmation).
//  - leaf listing (no known child name): wait for the folder's XHR to settle instead of
//    a bare sleep, so a slow-hydrating "money shot" frame can't be captured half-loaded.
async function openFolder(page, name, expectNext, holdMs = 1400) {
  await waitRow(page, name);
  await row(page, name).click();
  if (expectNext) {
    await waitRow(page, expectNext);
  } else {
    try { await page.waitForLoadState('networkidle', { timeout: NAV_TIMEOUT }); } catch { /* SPA polling — fall through to the hold */ }
  }
  await sleep(holdMs);
}

// Open the bucket via the select-bucket page (the only path that populates the file list).
async function openBucket(page) {
  await page.goto(SELECT, { waitUntil: 'domcontentloaded' });
  await openFolder(page, BUCKET, 'brandforge');
}

// Returns ms from context start until the bucket root first rendered (blank lead-in to trim).
async function run(page, startMs) {
  await openBucket(page);                                         // root: brandforge / brandkits / index
  const firstRenderMs = Date.now() - startMs;
  await sleep(4000);
  await openFolder(page, 'brandforge', 'runs');                  // hierarchical key strategy
  await openFolder(page, 'runs', RUN_DATE);
  await openFolder(page, RUN_DATE, RUN_UUID);                    // runs/<date>
  await openFolder(page, RUN_UUID, 'manifest.json');             // one run
  await sleep(5500);                                              // money shot: assets/ + manifest.json
  await openFolder(page, 'assets', null, 4500);                  // the generated asset objects
  await openBucket(page);                                         // back to root
  await openFolder(page, 'index', null, 6000);                   // one Parquet catalog (assets.parquet)
  return firstRenderMs;
}

fs.rmSync(tmpDir, { recursive: true, force: true });
fs.mkdirSync(tmpDir, { recursive: true });
fs.mkdirSync(rawShots, { recursive: true });

if (!fs.existsSync(STATE)) {
  console.error(`ERROR: storageState not found at ${STATE}`);
  process.exit(1);
}

console.log(`recording 06-b2-console (headless 1920x1080), bucket ${BUCKET}`);
let ok = true;
let firstRenderMs = 0;
let webm;
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({
    storageState: STATE,
    viewport: { width: W, height: H },
    deviceScaleFactor: 1,
    recordVideo: { dir: tmpDir, size: { width: W, height: H } },
  });
  const page = await context.newPage();
  const startMs = Date.now();
  try {
    firstRenderMs = await run(page, startMs);
  } catch (e) {
    ok = false;
    console.error('navigation error:', e.message);
  }
  const video = page.video();
  try { await context.close(); } finally { await browser.close(); }  // always reap Chromium
  webm = await video.path();
} finally {
  // The storageState holds live Backblaze session cookies (bearer-equivalent). Delete it
  // regardless of success/failure so it can't linger in the shared OS temp dir.
  fs.rmSync(STATE, { force: true });
}

// Trim the blank hydration lead-in so the clip starts on real content.
const skip = Math.max(0, firstRenderMs / 1000 - 1.0).toFixed(2);
console.log(`  first render at ${(firstRenderMs / 1000).toFixed(1)}s -> trimming ${skip}s lead-in`);
// A failed nav must NOT masquerade as a good take: assemble.py only checks existence/length,
// so on failure write a .FAILED.mp4 sidecar name and signal a non-zero exit instead.
const out = path.join(rawShots, ok ? '06-b2-console.mp4' : '06-b2-console.FAILED.mp4');
execFileSync(FFMPEG, [
  '-y', '-ss', String(skip), '-i', webm,
  '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
  '-an', out,
], { stdio: 'inherit' });
const mb = (fs.statSync(out).size / (1024 * 1024)).toFixed(2);
console.log(`  -> ${path.relative(projectRoot, out)} (${mb} MB)`);
if (!ok) {
  console.error('navigation incomplete — wrote 06-b2-console.FAILED.mp4; not used by assemble.py. Verify session/state and re-run.');
  process.exitCode = 1;
}
