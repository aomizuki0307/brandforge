// Render the self-contained HTML cards to exact 1920x1080 PNGs with Playwright.
// Reproducible companion to the interactive MCP; run from the AI_coding root so the
// bare `playwright` import resolves against its node_modules.
//
//   node workspace/projects/backblaze-genblaze/video/scripts/render_cards.mjs
//
import { chromium } from 'playwright';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const cardsDir = path.resolve(scriptDir, '..', 'cards');
const CARDS = ['title', 'prod', 'outro', 'placeholder'];
const W = 1920, H = 1080;

const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: W, height: H },
  deviceScaleFactor: 1,
});

for (const name of CARDS) {
  const html = path.join(cardsDir, `${name}.html`);
  if (!fs.existsSync(html)) {
    console.error(`missing card html: ${html}`);
    continue;
  }
  await page.goto(pathToFileURL(html).href, { waitUntil: 'networkidle' });
  const out = path.join(cardsDir, `${name}.png`);
  await page.screenshot({ path: out, clip: { x: 0, y: 0, width: W, height: H } });
  console.log(`rendered ${name}.png`);
}

await browser.close();
