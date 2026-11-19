#!/usr/bin/env node
/* Browser checks for a built Paper Assistant HTML brief.

   node skills/shared/html-brief/check_brief.cjs .artifacts/summarize-paper/<id>/summary.html

   Optional: needs a Playwright install. Set BRIEF_PLAYWRIGHT to the module
   path when `require('playwright')` does not resolve from here, and
   BRIEF_CHROMIUM to a browser binary when Playwright cannot find its own.
   Writes browser-check.json and screens/*.png next to the brief, and exits
   non-zero when a check fails. These checks cover the kit's behaviour; look at
   the screenshots yourself for anything paper-specific. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');

function loadPlaywright() {
  const candidates = [process.env.BRIEF_PLAYWRIGHT, 'playwright', 'playwright-core'].filter(Boolean);
  for (const name of candidates) {
    try { return require(name); } catch (error) { /* try the next one */ }
  }
  console.error('Playwright not found. Install it, or set BRIEF_PLAYWRIGHT=/path/to/node_modules/playwright.');
  process.exit(2);
}

const file = path.resolve(process.argv[2] || '');
if (!process.argv[2] || !fs.existsSync(file)) {
  console.error('Usage: check_brief.cjs <summary.html>');
  process.exit(2);
}
const outDir = path.join(path.dirname(file), 'screens');
fs.mkdirSync(outDir, { recursive: true });
const url = 'file://' + file;
const passed = [];
const failed = [];
const check = (name, ok, detail = '') => (ok ? passed : failed).push(detail && !ok ? `${name}: ${detail}` : name);

(async () => {
  const { chromium } = loadPlaywright();
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BRIEF_CHROMIUM || undefined });
  const remote = [];
  const errors = [];
  const open = async (options) => {
    const context = await browser.newContext({ reducedMotion: 'reduce', ...options });
    await context.route(/^https?:/, (route) => { remote.push(route.request().url()); return route.abort(); });
    const page = await context.newPage();
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
    await page.goto(url);
    return { context, page };
  };
  const noOverflow = (page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
  const widest = (page) => page.$$eval('main *', (nodes) => nodes
    .filter((n) => n.getBoundingClientRect().right > window.innerWidth + 1 && !n.closest('.table-wrap, pre, .eq'))
    .slice(0, 4).map((n) => n.localName + (n.id ? '#' + n.id : '') + (n.classList[0] ? '.' + n.classList[0] : '')).join(', '));

  try {
    /* Desktop: the primary reading layout. */
    const desktop = await open({ viewport: { width: 1440, height: 900 } });
    const page = desktop.page;
    check('Kit script ran', await page.evaluate(() => document.documentElement.classList.contains('js')));
    check('No horizontal overflow at 1440px', await noOverflow(page));
    await page.screenshot({ path: path.join(outDir, 'desktop-top.png') });
    const sections = await page.$$eval('main > section[id]', (nodes) => nodes.map((n) => n.id));
    for (const id of sections) {
      await page.evaluate((target) => document.getElementById(target).scrollIntoView(), id);
      await page.waitForTimeout(80);
      await page.screenshot({ path: path.join(outDir, `desktop-${id}.png`) });
    }
    check('Rail highlights the current section', await page.$$eval('.rail nav a[aria-current="true"]', (n) => n.length === 1));
    /* One screenshot per sub-heading, so every figure, table and control gets looked at. */
    for (const id of await page.$$eval('main h3[id], main .primer[id]', (nodes) => nodes.map((n) => n.id))) {
      await page.evaluate((target) => document.getElementById(target).scrollIntoView(), id);
      await page.waitForTimeout(60);
      await page.screenshot({ path: path.join(outDir, `desktop-h3-${id}.png`) });
    }

    const clipped = await page.$$eval('main *', (nodes) => nodes.filter((n) => {
      const style = getComputedStyle(n);
      return style.overflowX === 'visible' && n.scrollWidth > n.clientWidth + 2 && n.clientWidth > 0 &&
        !['svg', 'math', 'img', 'table', 'pre', 'code'].includes(n.localName) && !n.closest('svg, math, table, pre, .table-wrap, .eq');
    }).slice(0, 5).map((n) => n.localName + (n.id ? '#' + n.id : '') + (n.className ? '.' + String(n.className).split(' ')[0] : '')));
    check('No element overflows its box', clipped.length === 0, clipped.join(', '));

    const margin = await page.$('aside.mn');
    if (margin) {
      const overlap = await page.$$eval('aside.mn', (notes) => notes.some((note) => {
        const a = note.getBoundingClientRect();
        return [...note.parentElement.children].some((other) => {
          if (other === note || other.matches('aside.mn')) return false;
          const b = other.getBoundingClientRect();
          return a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1;
        });
      }));
      check('Margin notes do not overlap the text', !overlap);
    }

    const term = await page.$('a.term');
    if (term) {
      await term.scrollIntoViewIfNeeded();
      await term.hover();
      check('Glossary popover opens on hover', await page.$('.popover') !== null);
      await page.screenshot({ path: path.join(outDir, 'desktop-popover.png') });
      await page.mouse.move(2, 2);
    }

    const figure = await page.$('figure.paper-fig img');
    if (figure) {
      const visible = await figure.evaluate((img) => img.complete && img.naturalWidth > 0);
      check('Embedded figures decode', visible);
      if (await figure.isVisible()) {
        await figure.scrollIntoViewIfNeeded();
        await figure.click();
        check('Figure opens full size', await page.$eval('dialog.zoom', (d) => d.open));
        await page.keyboard.press('Escape');
      }
    }
    const broken = await page.$$eval('img', (imgs) => imgs.filter((i) => i.complete && i.naturalWidth === 0).length);
    check('Every image decodes', broken === 0, `${broken} broken`);

    const firstCheck = await page.$('details.check');
    if (firstCheck) {
      await firstCheck.scrollIntoViewIfNeeded();
      await firstCheck.$eval('summary', (s) => s.click());
      await firstCheck.$eval('[data-rate="again"]', (b) => b.click());
      check('Check-yourself points back to a section', await page.$eval('#check-summary', (n) => n.querySelector('a') !== null));
      await page.screenshot({ path: path.join(outDir, 'desktop-check-open.png') });
      await firstCheck.$eval('[data-rate="again"]', (b) => b.click());
    }

    const expand = await page.$('#expand-toggle');
    if (expand) {
      await expand.click();
      check('Expand opens every detail except answers', await page.$$eval('details:not(.check)', (d) => d.every((x) => x.open)));
      check('No horizontal overflow with details open', await noOverflow(page));
      await expand.click();
    }

    const theme = await page.$('#theme-toggle');
    if (theme) {
      await theme.click();
      const dark = await page.evaluate(() => document.documentElement.dataset.theme);
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: path.join(outDir, 'desktop-dark-top.png') });
      const mid = await page.$('#evidence, main > section:nth-of-type(3)');
      if (mid) { await mid.scrollIntoViewIfNeeded(); await page.screenshot({ path: path.join(outDir, 'desktop-dark-mid.png') }); }
      check('Theme toggle switches theme', dark === 'dark' || dark === 'light');
      await theme.click();
    }

    /* Every chip group must change something the reader can see. */
    const groups = await page.$$eval('.controls', (controls) => controls.length);
    let inert = 0;
    for (let g = 0; g < groups; g += 1) {
      const count = await page.$$eval('.controls', (controls, index) => controls[index].querySelectorAll('.chip').length, g);
      for (let c = 0; c < count; c += 1) {
        const before = await page.evaluate(() => document.querySelector('main').innerText);
        const pressedBefore = await page.$$eval('.controls', (controls, [gi, ci]) => controls[gi].querySelectorAll('.chip')[ci].getAttribute('aria-pressed'), [g, c]);
        await page.$$eval('.controls', (controls, [gi, ci]) => controls[gi].querySelectorAll('.chip')[ci].click(), [g, c]);
        const after = await page.evaluate(() => document.querySelector('main').innerText);
        const pressed = await page.$$eval('.controls', (controls, [gi, ci]) => controls[gi].querySelectorAll('.chip')[ci].getAttribute('aria-pressed'), [g, c]);
        if (pressed !== 'true') inert += 1;
        else if (pressedBefore !== 'true' && before === after) {
          const visual = await page.$$eval('.controls', (controls, gi) => controls[gi].dataset.visualOnly !== undefined, g);
          if (!visual) inert += 1;
        }
      }
    }
    check('Every control updates its text or caption', inert === 0, `${inert} chip(s) changed nothing readable`);

    /* Print: everything visible, details open. */
    const pdf = await page.pdf({ format: 'A4' });
    check('Prints to PDF', pdf.length > 20000);
    await page.emulateMedia({ media: 'print' });
    check('Print hides the rail', await page.$eval('.rail', (n) => getComputedStyle(n).display === 'none'));
    await page.emulateMedia({ media: 'screen' });
    await desktop.context.close();

    /* Narrower desktop and phone. */
    for (const [label, width, height] of [['laptop', 1180, 800], ['phone', 390, 844]]) {
      const view = await open({ viewport: { width, height } });
      check(`No horizontal overflow at ${width}px`, await noOverflow(view.page), await widest(view.page));
      await view.page.screenshot({ path: path.join(outDir, `${label}-top.png`) });
      const target = await view.page.$('#evidence, main > section:nth-of-type(3)');
      if (target) { await target.scrollIntoViewIfNeeded(); await view.page.screenshot({ path: path.join(outDir, `${label}-mid.png`) }); }
      await view.context.close();
    }

    /* No JavaScript: the brief must still read from top to bottom. */
    const plain = await open({ viewport: { width: 1440, height: 900 }, javaScriptEnabled: false });
    const words = await plain.page.$eval('main', (m) => m.innerText.split(/\s+/).length);
    check('Readable without JavaScript', words > 800, `${words} words visible`);
    check('JS-only controls hidden without JavaScript', await plain.page.$$eval('.js-only', (n) => n.every((x) => getComputedStyle(x).display === 'none')));
    check('Data tables present without JavaScript', await plain.page.$$eval('main table tbody tr', (rows) => rows.length > 0));
    await plain.page.screenshot({ path: path.join(outDir, 'nojs-top.png') });
    await plain.context.close();

    check('No JavaScript errors', errors.length === 0, errors.slice(0, 3).join(' | '));
    check('No network requests', remote.length === 0, remote.slice(0, 3).join(' | '));
  } finally {
    await browser.close();
  }
  const report = { ok: failed.length === 0, passed, failed, screens: fs.readdirSync(outDir).sort() };
  fs.writeFileSync(path.join(path.dirname(file), 'browser-check.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({ ok: report.ok, passed: passed.length, failed }, null, 2));
  process.exit(failed.length ? 1 : 0);
})().catch((error) => { console.error(error); process.exit(1); });
