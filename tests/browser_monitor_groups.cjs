// Run exclusively with monitor_groups_runtime.py in the disposable UI network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || '/qa/node_modules/playwright');
const base = 'http://app:8000';
(async () => {
  const browser = await chromium.launch({args: ['--no-sandbox']});
  const page = await browser.newPage({viewport: {width: 1366, height: 800}});
  const errors = [], checks = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base + '/teacher/login');
    await page.locator('#username').fill('teacher_ui');
    await page.locator('#password').fill('synthetic-ui-password');
    await Promise.all([page.waitForURL('**/teacher/dashboard'), page.locator('button[type=submit]').click()]);
    const sessions = await (await page.request.get(base + '/teacher/sessions?per_page=100')).text();
    // Find by the access code, without assuming a generated session ID.
    const match = sessions.match(/SCROLL[\s\S]*?href="\/teacher\/sessions\/(\d+)\/monitor"/);
    assert(match, 'Seed the long SCROLL monitor first');
    const path = `/teacher/sessions/${match[1]}/monitor`;
    await page.goto(base + path);
    await page.waitForFunction(() => document.querySelectorAll('.monitor-history-summary').length > 30);
    const baseline = process.env.BASELINE === '1';
    if (!baseline) {
      assert.equal(await page.locator('#monitor-grouping').inputValue(), 'student');
      const current = page.locator('#students-tbody .monitor-history-summary').filter({hasText: 'Учень 00'});
      assert.match(await current.innerText(), /Спроба #4/);
      assert.match(await current.innerText(), /Попередня оцінка: 9/);
      assert.equal(await current.getAttribute('data-status'), 'in_progress');
      assert.equal(await page.locator('#students-tbody .monitor-attempt-number').filter({hasText: 'Повторно'}).count(), 1);
      checks.push('Active fourth attempt has its own visible row, student group, previous grade and repeat label');
    }
    for (const width of [1366, 390]) {
      await page.setViewportSize({width, height: 800});
      const prefix = width < 768 ? '#monitor-mobile-cards' : '#students-tbody';
      const group = page.locator(`${prefix} .monitor-history-summary`).filter({hasText: 'Учень 26'});
      const button = group.locator('button');
      await button.scrollIntoViewIfNeeded();
      await page.waitForTimeout(300);
      const before = await button.evaluate(el => ({top: el.getBoundingClientRect().top, scroll: window.scrollY}));
      await button.click();
      await page.waitForTimeout(400);
      const after = await button.evaluate(el => ({top: el.getBoundingClientRect().top, scroll: window.scrollY}));
      checks.push({width, action: 'expand', before, after});
      if (!baseline) assert(Math.abs(before.top - after.top) < 3, JSON.stringify({width, before, after}));
      const stable = await button.evaluate(el => ({top: el.getBoundingClientRect().top, scroll: window.scrollY}));
      const movements = await page.evaluate(async () => {
        let count = 0;
        const observer = new MutationObserver(entries => {count += entries.reduce((sum, e) => sum + e.addedNodes.length + e.removedNodes.length, 0);});
        observer.observe(document.getElementById('students-tbody'), {childList: true});
        for (let i = 0; i < 10; i++) await fetchSessionStatus();
        await new Promise(resolve => requestAnimationFrame(resolve));
        observer.disconnect(); return count;
      });
      await page.waitForTimeout(300);
      const refreshed = await button.evaluate(el => ({top: el.getBoundingClientRect().top, scroll: window.scrollY}));
      checks.push({width, action: 'refresh10', stable, refreshed, movements});
      if (!baseline) {
        assert(Math.abs(stable.top - refreshed.top) < 3, JSON.stringify({width, stable, refreshed}));
        assert.equal(movements, 0, 'Unchanged refresh must not move rows');
        assert.equal(await button.getAttribute('aria-expanded'), 'true');
        assert(await button.evaluate(el => document.activeElement === el), 'Refresh keeps keyboard focus');
      }
      await button.click();
      await page.waitForTimeout(200);
      if (!baseline) {
        const collapsed = await button.evaluate(el => el.getBoundingClientRect().top);
        assert(Math.abs(stable.top - collapsed) < 3, JSON.stringify({width, collapsed, stable}));
      }
    }
    if (!baseline) {
      for (const mode of ['all', 'history', 'student']) {
        await page.locator('#monitor-grouping').selectOption(mode);
        await page.evaluate(() => fetchSessionStatus());
        if (mode === 'all') assert.equal(await page.locator('.monitor-history-summary').count(), 0);
        await page.reload();
        assert.equal(await page.locator('#monitor-grouping').inputValue(), mode);
      }
      checks.push('All three display modes persist through HTTP refresh and reload, without changing attempts');
      for (const width of [320, 390, 768, 1366]) {
        await page.setViewportSize({width, height: 800});
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 2));
      }
      await page.screenshot({path: '/artifacts/monitor-student-groups.png', fullPage: false});
      const snapshot = await (await page.request.get(base + `/api/session-status/${match[1]}`)).json();
      const active = snapshot.attempts.find(a => a.status === 'in_progress');
      assert.equal((await page.request.post(base + `/teacher/attempts/${active.id}/stop`)).status(), 200);
      await page.evaluate(() => fetchSessionStatus());
      const completed = page.locator('#students-tbody .monitor-history-summary').filter({hasText: 'Учень 00'});
      assert.match(await completed.locator('button').innerText(), /\(4\)/);
      assert(!await page.locator(`#row-${active.id}`).isVisible());
      checks.push('Completion moves the fourth attempt into its existing history group');
      for (const width of [1366, 390]) {
        await page.setViewportSize({width, height: 800});
        await page.goto(base + `/teacher/results?session_id=${match[1]}`);
        const button = page.locator('.toggle-btn').nth(12);
        await button.scrollIntoViewIfNeeded();
        await page.waitForTimeout(200);
        const top = await button.evaluate(el => el.getBoundingClientRect().top);
        await button.click();
        await page.waitForTimeout(300);
        const expanded = await button.evaluate(el => el.getBoundingClientRect().top);
        assert(Math.abs(top - expanded) < 3, JSON.stringify({width, top, expanded}));
        assert.equal(await button.getAttribute('aria-expanded'), 'true');
      }
      checks.push('Expanding completed groups in Results also keeps the viewport on desktop and phone');
    }
    assert.deepEqual(errors, []);
    fs.writeFileSync(`/artifacts/${baseline ? 'before' : 'after'}-monitor.json`, JSON.stringify({checks, errors}, null, 2));
    console.log(JSON.stringify({checks, errors}));
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
