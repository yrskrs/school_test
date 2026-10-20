// Run against the disposable tests/compose.runtime.yaml project only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = 'http://app:8000';
const artifacts = process.env.AUDIT_ARTIFACTS || '/artifacts';

(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  const errors = [], checks = [], layouts = [];
  const page = await browser.newPage({viewport: {width: 1366, height: 768}});
  page.on('pageerror', error => errors.push({page: page.url(), message: error.message, stack: error.stack}));
  page.on('dialog', dialog => dialog.accept());
  fs.mkdirSync(artifacts, {recursive: true});
  const scaleButton = action => page.locator(`[data-scale-action="${action}"]`);
  async function scaleTo(value) {
    await scaleButton('reset').click();
    for (let current = 100; current !== value; current += value < 100 ? -10 : 10) {
      await scaleButton(value < 100 ? 'decrease' : 'increase').click();
    }
    assert.equal(await page.locator('#page-scale-value').innerText(), value + '%');
  }
  async function fit(label) {
    // ResizeObserver consumers (including Chart.js) update on the next frame.
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const dimensions = await page.evaluate(() => {
      const doc = document.documentElement;
      return {width: innerWidth, client: doc.clientWidth, scroll: doc.scrollWidth,
        bodyRight: document.body.getBoundingClientRect().right, scale: Number(doc.style.zoom)};
    });
    layouts.push({page: label, ...dimensions});
    for (const stat of await page.locator('.sidebar-stat-label').evaluateAll(labels => labels.map(label => {
      const range = document.createRange();
      range.selectNodeContents(label);
      return {text: label.textContent.trim(), lines: range.getClientRects().length,
        width: range.getBoundingClientRect().width, available: label.parentElement.getBoundingClientRect().width};
    }))) {
      assert.equal(stat.lines, 1, JSON.stringify(stat));
      assert(stat.width <= stat.available + 1, JSON.stringify(stat));
    }
    if (dimensions.scroll > dimensions.client + 1) {
      const overflow = await page.evaluate(() => Array.from(document.querySelectorAll('body *')).filter(el =>
        el.getBoundingClientRect().right > innerWidth + 1).slice(0, 15).map(el => ({
          tag: el.tagName, id: el.id, class: el.className, right: el.getBoundingClientRect().right})));
      assert.fail(JSON.stringify({label, ...dimensions, overflow}));
    }
  }
  try {
    if (!process.env.DISPLAY_TEACHER_ONLY) {
    let rosterRequests = 0;
    page.on('request', request => {if (request.url().includes('/api/session-roster')) rosterRequests++;});
    await page.goto(base + '/student/login?code=VIEW01');
    assert.equal(await page.locator('#session-meta-questions').innerText(), 'Усього в тесті: 200');
    assert.equal(await page.locator('#session-meta-assigned').innerText(), 'Буде задано: 25');
    assert.equal(rosterRequests, 0);
    await scaleTo(80);
    await page.reload();
    assert.equal(await page.locator('#page-scale-value').innerText(), '80%');
    await scaleTo(80);
    assert(await scaleButton('decrease').isDisabled());
    await scaleTo(120);
    assert(await scaleButton('increase').isDisabled());
    for (const [stored, expected] of [[60, 80], [150, 120]]) {
      await page.evaluate(value => localStorage.setItem('schooltest:page-scale', String(value)), stored);
      await page.reload();
      assert.equal(await page.locator('#page-scale-value').innerText(), expected + '%');
    }
    for (const [width, height] of [[1366, 768], [1024, 600], [800, 600], [640, 480], [390, 844], [320, 568]]) {
      await page.setViewportSize({width, height});
      for (const scale of [80, 100, 120]) {await scaleTo(scale); await fit('student-login');}
    }
    await page.setViewportSize({width: 640, height: 480});
    await scaleTo(80);
    await page.screenshot({path: artifacts + '/login_640x480_80.png'});
    checks.push('200 total / 25 assigned; no duplicate initial request; scale persists, resets, and respects 80–120% bounds; 18 login layouts fit');
    console.log('PASS: question counts, scale persistence and 18 login layouts');

    await page.goto(base + '/student/login?manual=1');
    const before = rosterRequests;
    await page.locator('#access_code').pressSequentially('VIEW01', {delay: 30});
    await page.locator('#session-meta-assigned').waitFor();
    assert.equal(await page.locator('#session-meta-assigned').innerText(), 'Буде задано: 25');
    assert.equal(rosterRequests - before, 1);
    let delayedDone;
    const delayed = new Promise(resolve => {delayedDone = resolve;});
    await page.route('**/api/session-roster?code=OLD123', async route => {
      await new Promise(resolve => setTimeout(resolve, 700));
      await route.fulfill({json: {title: 'Застаріла відповідь', question_count: 99,
        assigned_question_count: 9, students: [], available: false}}).catch(() => {});
      delayedDone();
    });
    const oldRequest = page.waitForRequest(request => request.url().includes('code=OLD123'));
    await page.locator('#access_code').fill('OLD123');
    await oldRequest;
    await page.locator('#access_code').fill('VIEW01');
    await delayed;
    await page.waitForFunction(() => !document.getElementById('btn-submit-login').disabled);
    assert.equal(await page.locator('#session-meta-assigned').innerText(), 'Буде задано: 25');
    await page.unroute('**/api/session-roster?code=OLD123');
    await page.route('**/api/session-roster?code=NO_NET', route => route.abort('failed'));
    await page.locator('#access_code').fill('NO_NET');
    await page.waitForFunction(() => document.getElementById('roster-status').textContent.includes('Не вдалося'));
    assert(await page.locator('#btn-submit-login').isDisabled());
    await page.unroute('**/api/session-roster?code=NO_NET');
    await page.locator('#access_code').fill('VIEW01');
    await page.waitForFunction(() => !document.getElementById('btn-submit-login').disabled);
    checks.push('Late cancelled roster response cannot replace the current code; network failure blocks submission and recovery restores it');
    await page.locator('#student_name').fill('Перевірка Учень');
    await page.locator('#btn-submit-login').click();
    await page.waitForURL('**/student/instruction/*');
    assert.equal(await page.locator('.istat-value').first().innerText(), '25');
    await fit('instruction');
    await page.locator('.btn-start').click();
    await page.waitForURL('**/student/test/*');
    await page.setViewportSize({width: 1366, height: 768});
    await page.getByRole('button', {name: /Розпочати|Почати|Увімкнути/}).filter({hasText: /тест|екран/}).first().click();
    await page.waitForFunction(() => !!document.fullscreenElement);
    const theme = page.locator('.test-theme-toggle');
    assert.equal((await theme.innerText()).trim(), '');
    assert.equal(await theme.locator('svg:visible').count(), 1);
    const selected = page.locator('.option-item input').first();
    await page.locator('.option-item').first().click();
    const selectedValue = await selected.inputValue();
    for (const osTheme of ['dark', 'light']) {
      await page.emulateMedia({colorScheme: osTheme});
      for (const desired of ['light', 'dark']) {
        if (await page.locator('html').getAttribute('data-theme') !== desired) await theme.click();
        await page.waitForFunction(desired => {
          const color = getComputedStyle(document.querySelector('.option-item:last-child')).backgroundColor;
          const channels = color.match(/\d+/g).slice(0, 3).map(Number);
          return desired === 'light' ? channels.every(c => c > 230) : channels.every(c => c < 80);
        }, desired);
        const colors = await page.locator('.option-item').last().evaluate(label => ({
          background: getComputedStyle(label).backgroundColor,
          scheme: getComputedStyle(label.querySelector('input')).colorScheme,
        }));
        assert.equal(colors.scheme, desired);
        const channels = colors.background.match(/\d+/g).slice(0, 3).map(Number);
        assert(desired === 'light' ? channels.every(c => c > 230) : channels.every(c => c < 80), JSON.stringify(colors));
        assert(await selected.isChecked());
        assert.equal(await selected.inputValue(), selectedValue);
        if (osTheme === 'dark') await page.screenshot({path: artifacts + '/answers_' + desired + '.png', animations: 'disabled'});
      }
    }
    checks.push('Answer backgrounds and native radio controls follow explicit light/dark theme under both OS themes; selected answer survives switching');
    const oldTheme = await page.locator('html').getAttribute('data-theme');
    await theme.click();
    assert.notEqual(await page.locator('html').getAttribute('data-theme'), oldTheme);
    assert.equal(await theme.locator('svg:visible').count(), 1);
    for (const [width, height] of [[1366, 768], [1024, 600], [640, 480], [390, 844], [320, 568]]) {
      await page.setViewportSize({width, height});
      for (const scale of [80, 100, 120]) {await scaleTo(scale); await fit('take-test');}
    }
    await page.setViewportSize({width: 1024, height: 600});
    await scaleTo(80);
    await page.screenshot({path: artifacts + '/test_1024x600_80.png'});
    assert.equal(await page.locator('#total-q-num').innerText(), '25');
    checks.push('One request for six typed code characters; instruction and actual test both assign 25; SVG theme toggles; 15 test layouts fit in fullscreen and all sidebar labels stay on one line');
    console.log('PASS: debounced code, instruction, SVG theme and 15 test layouts');

    // UI completion verifies that scale/theme controls preserve answers and anti-cheat state.
    for (let i = 0; i < 25; i++) {
      const number = await page.locator('#current-q-num').innerText();
      await page.locator('.option-label').filter({hasText: 'Правильно'}).first().click();
      await page.locator('#btn-next').click();
      await page.waitForFunction(number => document.getElementById('current-q-num').textContent !== number
        || document.getElementById('finish-modal').style.display !== 'none', number);
    }
    await page.locator('#finish-modal').waitFor({state: 'visible'});
    await page.locator('#finish-modal .btn-danger').click();
    await page.waitForURL('**/finished');
    checks.push('Completed all 25 answers through the UI after scaling and switching theme');
    console.log('PASS: completed 25 answers');
    }

    await page.goto(base + '/teacher/login');
    await page.locator('[name=username]').fill('display_check');
    await page.locator('[name=password]').fill('synthetic-display-password');
    await page.locator('button[type=submit]').click();
    await page.waitForURL('**/teacher/dashboard');
    const teacherPaths = ['/teacher/dashboard', '/teacher/tests', '/teacher/sessions', '/teacher/results', '/teacher/roster-sync', '/teacher/tests/create', '/teacher/about', '/teacher/profile', '/teacher/integrations', '/teacher/updates', `/teacher/sessions/${process.env.DISPLAY_SESSION_ID || 1}/monitor`];
    for (const path of teacherPaths) {
      await page.goto(base + path);
      for (const [width, height] of [[1366, 768], [640, 480], [390, 844]]) {
        await page.setViewportSize({width, height});
        for (const scale of [80, 100, 120]) {await scaleTo(scale); await fit(path);}
      }
      if (path === '/teacher/roster-sync') {
        for (const tab of ['classes', 'sync', 'outside']) {
          await page.locator(`#tab-btn-${tab}`).click();
          await fit('roster-' + tab);
        }
      }
    }
    checks.push('Eleven teacher pages reflow at desktop, compact and mobile sizes at 80/100/120%; roster tabs fit too');

    const blocked = await browser.newPage();
    blocked.on('pageerror', error => errors.push(error.message));
    await blocked.addInitScript(() => {
      Object.defineProperty(window, 'localStorage', {get() {throw new Error('Storage blocked');}});
    });
    await blocked.goto(base + '/student/login?code=VIEW01');
    await blocked.locator('[data-scale-action="decrease"]').click();
    assert.equal(await blocked.locator('#page-scale-value').innerText(), '90%');
    await blocked.locator('#floating-theme-btn').click();
    await blocked.close();
    checks.push('Scale and theme still work when localStorage is blocked');
    assert.deepEqual(errors, []);
    fs.writeFileSync(artifacts + '/browser_checks.json', JSON.stringify({checks, layouts, javascript_errors: errors}, null, 2) + '\n');
    console.log(JSON.stringify({checks, layouts: layouts.length, javascript_errors: errors}, null, 2));
  } catch (error) {
    await page.screenshot({path: artifacts + '/failure.png', fullPage: true}).catch(() => {});
    fs.writeFileSync(artifacts + '/failure.json', JSON.stringify({error: error.message, layouts, javascript_errors: errors}, null, 2));
    throw error;
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
