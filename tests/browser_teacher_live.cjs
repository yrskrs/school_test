// Read-only deployment smoke check. Session input stays outside the repository.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.UI_BASE;
assert(base && process.env.UI_SESSION_FILE, 'Explicit site and private session file are required');
(async () => {
  const sessions = JSON.parse(fs.readFileSync(process.env.UI_SESSION_FILE, 'utf8'));
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  const checks = [], errors = [];
  try {
    for (const session of sessions) {
      const context = await browser.newContext({viewport: {width: 1440, height: 900}});
      await context.addCookies([{name: session.name, value: session.value, url: base, httpOnly: true, sameSite: 'Lax'}]);
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      for (const path of session.paths) {
        const response = await page.goto(base + path);
        assert.equal(response.status(), 200, path);
        assert(await page.locator('.teacher-workspace').count(), path);
        for (const width of [1440, 768, 390]) {
          await page.setViewportSize({width, height: 900});
          await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
          const fit = await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 2);
          assert(fit, `${path} at ${width}`);
          checks.push({role: session.role, path, width, status: 200, fit});
        }
      }
      await context.close();
    }
    assert.equal(errors.length, 0, JSON.stringify(errors));
    fs.writeFileSync('/artifacts/live-checks.json', JSON.stringify({checks, errors}, null, 2));
    console.log(JSON.stringify({checks: checks.length, errors}));
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
