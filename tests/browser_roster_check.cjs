// Real Chromium UI checks against the disposable upgrade-audit app only.
// PLAYWRIGHT_MODULE may point to a temporary install; no production URL is accepted.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = 'http://app:8000';
const artifacts = process.env.AUDIT_ARTIFACTS || '/artifacts';

(async () => {
  fs.mkdirSync(artifacts, {recursive: true});
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  const errors = [];
  const checks = [];
  function watch(page) {page.on('pageerror', error => errors.push(error.message)); return page;}
  async function visible(page, selector) {await page.locator(selector).waitFor({state: 'visible'});}
  const teacher = await browser.newContext();
  const page = watch(await teacher.newPage());
  const testTitle = 'Browser test 10-В ' + Date.now();
  try {
    await page.goto(base + '/teacher/login');
    await page.locator('[name=username]').fill('roster_audit');
    await page.locator('[name=password]').fill('synthetic-roster-password');
    await page.locator('button[type=submit]').click();
    await page.waitForURL('**/teacher/dashboard');
    await page.goto(base + '/teacher/tests/create');
    const options = await page.locator('#class_name option').evaluateAll(nodes => nodes.map(node => node.value));
    for (const value of ['10-А', '11-А', '10-В']) assert(options.includes(value), value);
    await page.locator('#title').fill(testTitle);
    await page.locator('#class_name').selectOption('10-В');
    await page.locator('#max_attempts').fill('2');
    await page.getByRole('button', {name: /Додати питання/}).click();
    await page.getByPlaceholder('Введіть питання...').fill('2 + 2?');
    await page.getByRole('button', {name: /Додати варіант/}).click();
    await page.getByRole('button', {name: /Додати варіант/}).click();
    await page.getByPlaceholder('Варіант відповіді').nth(0).fill('4');
    await page.getByPlaceholder('Варіант відповіді').nth(1).fill('5');
    await page.locator('.options-list input[type=radio]').first().check();
    await page.getByRole('button', {name: 'Зберегти тест', exact: true}).click();
    await page.waitForURL('**/teacher/tests/*/edit?created=1');
    assert.equal(await page.locator('#class_name').inputValue(), '10-В');
    await page.goto(base + '/teacher/tests');
    const row = page.locator('tr').filter({hasText: testTitle});
    await row.getByRole('button', {name: 'Запустити', exact: true}).click();
    await page.waitForURL('**/teacher/sessions/*/monitor');
    const sessionId = Number(page.url().split('/sessions/')[1].split('/')[0]);
    const code = (await page.locator('.code-big').textContent()).trim();
    checks.push('Teacher creates a question, selects imported 10-В, saves and launches through the real UI');

    const student = await browser.newContext({viewport: {width: 390, height: 844}});
    const pupil = watch(await student.newPage());
    await pupil.goto(base + '/student/login?code=' + code);
    await pupil.waitForFunction(() => !document.getElementById('btn-submit-login').disabled);
    assert.equal(await pupil.locator('#roster_student_id option').count(), 18);
    assert(await pupil.locator('#manual-name-group').isHidden());
    assert(await pupil.locator('#other-student-toggle-wrap').isHidden());
    assert(await pupil.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
    await pupil.screenshot({path: artifacts + '/student_roster_mobile.png', fullPage: true});
    const pupilId = await pupil.locator('#roster_student_id option').nth(2).getAttribute('value');
    await pupil.locator('#roster_student_id').selectOption(pupilId);
    await pupil.locator('#btn-submit-login').click();
    await pupil.waitForURL('**/student/instruction/*');
    for (let attempt = 1; attempt <= 2; attempt++) {
      await pupil.getByRole('link', {name: /Почати тест/}).click();
      await pupil.waitForURL(/\/student\/test\/\d+$/);
      const attemptId = Number(pupil.url().split('/test/')[1]);
      await pupil.getByRole('button', {name: 'Розпочати тестування'}).click();
      await pupil.locator('.option-item').filter({hasText: /^\s*4\s*$/}).click();
      await pupil.waitForFunction(() => document.getElementById('student-sync-status').dataset.state === 'saved');
      await pupil.locator('#btn-finish').click();
      await visible(pupil, '#confirm-finish-btn');
      await pupil.locator('#confirm-finish-btn').click();
      await pupil.waitForURL('**/student/test/*/finished');
      const result = await (await teacher.request.get(base + '/api/attempt/' + attemptId)).json();
      assert.equal(result.status, 'finished');
      assert.equal(result.score, result.max_score);
      assert.equal(result.score, 1);
      const retake = pupil.getByRole('button', {name: 'Повторно пройти тест'});
      if (attempt === 1) {
        await retake.click();
        await pupil.waitForURL('**/student/instruction/*');
      } else {
        assert.equal(await retake.count(), 0);
        assert((await pupil.locator('body').textContent()).includes('Використано проходжень: 2 з 2'));
      }
    }
    const state = await (await teacher.request.get(base + '/api/session-status/' + sessionId)).json();
    assert.equal(state.attempts.length, 2);
    checks.push('Mobile pupil list: 17 pupils, no manual bypass; answer saved and graded, two attempts then limit enforced');

    const visitor = await browser.newContext();
    const login = watch(await visitor.newPage());
    await login.goto(base + '/student/login?code=INVALID');
    assert(await login.locator('#access_code').isVisible());
    assert((await login.locator('body').textContent()).includes('Невірний код доступу або сесія завершена'));
    await login.goto(base + '/student/login');
    await login.getByRole('link', {name: 'Ввести інший код тесту'}).click();
    await visible(login, '#access_code');
    checks.push('Invalid URL code cannot switch to a pinned test; pupil can enter another code from a pinned page');

    await login.route('**/api/session-roster?*', async route => {
      if (route.request().url().includes('BROWS1')) await new Promise(resolve => setTimeout(resolve, 400));
      await route.continue();
    });
    await login.locator('#access_code').fill('BROWS1');
    assert(await login.locator('#btn-submit-login').isDisabled());
    await login.waitForFunction(() => document.getElementById('roster_student_id').options.length === 18 && !document.getElementById('btn-submit-login').disabled);
    await login.locator('#access_code').fill('BROWS1');
    await login.locator('#access_code').fill('INVALID');
    await login.waitForFunction(() => document.getElementById('roster-status').textContent.includes('Невірний код'));
    await login.waitForTimeout(500);
    assert(await login.locator('#btn-submit-login').isDisabled());
    assert(await login.locator('#roster-select-group').isHidden());
    checks.push('Loading prevents early submission; a late roster response cannot overwrite a newer invalid code');

    await login.unroute('**/api/session-roster?*');
    await login.route('**/api/session-roster?*', route => route.abort('failed'));
    await login.goto(base + '/student/login?manual=1');
    await login.locator('#access_code').fill('BROWS1');
    await login.waitForFunction(() => document.getElementById('roster-status').textContent.includes('Не вдалося'));
    assert(await login.locator('#btn-submit-login').isDisabled());
    await login.goto(base + '/student/login?code=BROWS1');
    await login.waitForFunction(() => document.getElementById('roster-status').textContent.includes('останній отриманий'));
    assert.equal(await login.locator('#roster_student_id option').count(), 18);
    assert(await login.locator('#btn-submit-login').isEnabled());
    checks.push('Network failure is explained; a server-rendered known roster remains usable if refresh fails');
    assert.deepEqual(errors, []);
    fs.writeFileSync(artifacts + '/browser_checks.json', JSON.stringify({passed: checks, javascript_errors: errors}, null, 2));
    console.log(JSON.stringify({passed: checks.length, checks, javascript_errors: errors}, null, 2));
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
