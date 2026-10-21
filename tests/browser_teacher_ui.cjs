// Synthetic browser audit in the isolated schooltest-teacher-ui Compose project.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.UI_BASE || 'http://app:8000';
const artifacts = process.env.AUDIT_ARTIFACTS || '/artifacts';
const baseline = !!process.env.UI_BASELINE;
const pages = {
  dashboard: '/teacher/dashboard', tests: '/teacher/tests', sessions: '/teacher/sessions',
  results: '/teacher/results', attempts: '/teacher/results?view_mode=all',
  archive: '/teacher/tests?tab=archive', 'session-archive': '/teacher/sessions?tab=archive',
  'result-archive': '/teacher/results?tab=archive', journal: '/teacher/journal',
  create: '/teacher/tests/create', import: '/teacher/tests/import', edit: '/teacher/tests/1/edit',
  monitor: '/teacher/sessions/1/monitor', analytics: '/teacher/tests/1/analytics',
  detail: '/teacher/results/1', roster: '/teacher/roster-sync',
  integrations: '/teacher/integrations', profile: '/teacher/profile', about: '/teacher/about',
};
(async () => {
  fs.mkdirSync(artifacts, {recursive: true});
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  const errors = [], layouts = [], checks = [], externalRequests = [];
  const widths = new Map();
  const page = await browser.newPage({viewport: {width: 1440, height: 900}});
  page.on('pageerror', e => errors.push({url: page.url(), error: e.message}));
  page.on('request', request => {
    if (/^https?:/.test(request.url()) && new URL(request.url()).origin !== new URL(base).origin) externalRequests.push(request.url());
  });
  async function login(username) {
    await page.goto(base + '/teacher/login');
    await page.locator('#username').fill(username);
    await page.locator('#password').fill('synthetic-ui-password');
    await Promise.all([page.waitForURL('**/teacher/dashboard'), page.locator('button[type=submit]').click()]);
  }
  async function fit(label) {
    await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
    const layout = await page.evaluate(() => {
      const doc = document.documentElement, main = document.querySelector('main');
      const scrollContainers = '.table-wrapper,.table-responsive,.journal-wrapper,.roster-student-table-wrap,pre,.terminal-output';
      const problems = Array.from(document.querySelectorAll('main *, .teacher-toolbar > *, #navbar-teacher-dropdown, #notifications-dropdown')).filter(el => {
        const r = el.getBoundingClientRect();
        if (!r.width || !r.height || !el.checkVisibility() || el.closest(scrollContainers)) return false;
        return r.right > innerWidth + 2 || r.left < -2;
      }).slice(0, 12).map(el => ({tag: el.tagName, id: el.id, class: el.className}));
      return {viewport: innerWidth, client: doc.clientWidth, scroll: doc.scrollWidth,
        main: Math.round(main.getBoundingClientRect().width), problems};
    });
    layouts.push({label, ...layout});
    if (!baseline && !process.env.UI_COLLECT) {
      assert(layout.scroll <= layout.client + 2, JSON.stringify({label, ...layout}));
      assert.equal(layout.problems.length, 0, JSON.stringify({label, ...layout}));
      const suffix = label.split('/').slice(-3).join('/');
      if (!label.startsWith('login/') && /^(light|dark)\//.test(suffix)) {
        if (widths.has(suffix)) assert.equal(layout.main, widths.get(suffix), `Shared workspace width: ${label}`);
        else widths.set(suffix, layout.main);
      }
    }
  }
  try {
    await page.goto(base + '/teacher/login');
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({width, height: 900});
      for (const theme of ['light', 'dark']) for (const scale of [80, 100, 120]) {
        await page.evaluate(({theme, scale}) => {
          document.documentElement.dataset.theme = theme;
          document.documentElement.style.zoom = String(scale / 100);
          document.documentElement.style.setProperty('--page-scale', String(scale / 100));
        }, {theme, scale});
        await fit(`login/${theme}/${width}/${scale}`);
      }
    }
    if (!baseline) {
      await page.locator('#username').fill('teacher_ui');
      await page.locator('#password').fill('synthetic-wrong-password');
      const [response] = await Promise.all([page.waitForNavigation(), page.locator('button[type=submit]').click()]);
      assert.equal(response.status(), 401);
      await page.evaluate(() => {document.documentElement.style.zoom = '1.2'; document.documentElement.style.setProperty('--page-scale', '1.2');});
      await page.locator('.alert').evaluateAll(alerts => alerts.forEach(alert => alert.getAnimations().forEach(animation => animation.finish())));
      await fit('login-error');
    }
    await login('teacher_ui');
    for (const [label, path] of Object.entries(pages)) {
      if (process.env.UI_PAGES && !process.env.UI_PAGES.split(',').includes(label)) continue;
      const response = await page.goto(base + path);
      assert.equal(response.status(), 200, label);
      if (!baseline && label === 'dashboard') assert(await page.evaluate(() => typeof Chart === 'function'), 'Local offline chart library');
      for (const theme of ['light', 'dark']) {
        for (const width of [1440, 1024, 768, 390, 320]) {
          await page.setViewportSize({width, height: width < 500 ? 844 : 900});
          for (const scale of [80, 100, 120]) {
            await page.evaluate(({theme, scale}) => {
              document.documentElement.dataset.theme = theme;
              document.documentElement.style.zoom = String(scale / 100);
              document.documentElement.style.setProperty('--page-scale', String(scale / 100));
            }, {theme, scale});
            await fit(`${label}/${theme}/${width}/${scale}`);
          }
          if ([1440, 390].includes(width)) {
            await page.evaluate(() => {document.documentElement.style.zoom = '1'; document.documentElement.style.setProperty('--page-scale', '1');});
            await page.screenshot({path: `${artifacts}/${label}-${theme}-${width}.png`, fullPage: true, animations: 'disabled'});
          }
        }
      }
    }
    checks.push('All teacher routes, both themes, 5 viewports × 3 scales');
    await page.setViewportSize({width: 390, height: 844});
    await page.goto(base + '/teacher/dashboard');
    if (!baseline) {
      await page.locator('#teacher-menu-toggle').click();
      assert.equal(await page.locator('#teacher-menu-toggle').getAttribute('aria-expanded'), 'true');
      await page.locator('#teacher-navigation').getByRole('link', {name: 'Тести', exact: true}).click();
      await page.waitForURL('**/teacher/tests');
      await page.locator('.list-menu summary').first().click();
      await fit('test-actions-open');
      await page.locator('.list-menu summary').first().press('Escape');
      assert.equal(await page.locator('.list-menu[open]').count(), 0);
      checks.push('Mobile menu, navigation, secondary actions and Escape');
      await page.locator('#navbar-teacher-toggle').click();
      await page.locator('#navbar-teacher-list button').first().waitFor();
      await fit('profile-switcher-open');
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('#navbar-teacher-toggle').getAttribute('aria-expanded'), 'false');
      await page.locator('#notifications-toggle').click();
      await fit('notifications-open');
      await page.keyboard.press('Escape');

      for (const tab of ['classes', 'sync', 'outside', 'students']) {
        await page.goto(base + '/teacher/roster-sync');
        await page.locator(`#tab-btn-${tab}`).click();
        assert(await page.locator(`#tab-pane-${tab}`).isVisible());
        for (const width of [1440, 390, 320]) {
          await page.setViewportSize({width, height: 900});
          for (const scale of [100, 120]) {
            await page.evaluate(scale => {document.documentElement.style.zoom = String(scale / 100); document.documentElement.style.setProperty('--page-scale', String(scale / 100));}, scale);
            await fit(`roster-tab-${tab}/${width}/${scale}`);
          }
        }
      }
      await page.locator('#btnAddStudentToggle').click();
      await fit('roster-add-form');
      await page.locator('#addStudentCard').getByRole('button', {name: 'Скасувати'}).click();
      if (!await page.locator('#accordion-body-1').isVisible()) await page.locator('.roster-accordion-header').first().click();
      await fit('roster-class-open');
      await page.locator('[onclick="toggleEditStudent(1, true)"]').click();
      await fit('roster-student-edit');
      checks.push('Profile switcher, notifications, all roster tabs, add/edit forms and expanded class');

      await page.goto(base + '/teacher/results');
      await page.getByRole('button', {name: /Розгорнути всі/}).click();
      await fit('results-expanded');
      const download = page.waitForEvent('download');
      await page.getByRole('link', {name: /Завантажити CSV/}).click();
      assert((await download).suggestedFilename().endsWith('.csv'));
      await page.goto(base + '/teacher/tests');
      await page.locator('#list-search').fill('НемаєТакогоТесту');
      await page.getByRole('button', {name: 'Знайти', exact: true}).click();
      await page.getByRole('heading', {name: 'Нічого не знайдено'}).waitFor();
      await page.goto(base + '/teacher/tests/create');
      await page.locator('.editor-sticky-actions button[onclick="addQuestion()"]').click();
      assert.equal(await page.locator('.question-editor').count(), 1);
      await fit('editor-added-question');
      await page.goto(base + '/teacher/profile');
      await page.locator('#subject').fill('Синтетичний предмет');
      await page.locator('#saveProfileBtn').click();
      await page.waitForFunction(() => !document.getElementById('saveProfileBtn').disabled);
      await page.reload();
      assert.equal(await page.locator('#subject').inputValue(), 'Синтетичний предмет');
      for (const action of ['decrease', 'decrease']) await page.locator(`[data-scale-action="${action}"]`).click();
      assert.equal(await page.locator('#page-scale-value').innerText(), '80%');
      await page.reload();
      assert.equal(await page.locator('#page-scale-value').innerText(), '80%');
      await page.locator('[data-scale-action="reset"]').click();
      const oldTheme = await page.locator('html').getAttribute('data-theme');
      await page.locator('#navbar-theme-btn').click();
      assert.notEqual(await page.locator('html').getAttribute('data-theme'), oldTheme);
      await page.reload();
      assert.notEqual(await page.locator('html').getAttribute('data-theme'), oldTheme);
      checks.push('Expanded result groups, CSV, filters, adding a question, profile save, scale/theme persistence');
    }
    await login('admin');
    for (const path of ['/teacher/dashboard', '/teacher/profile', '/teacher/logs', '/teacher/updates']) {
      assert.equal((await page.goto(base + path)).status(), 200);
      for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => {document.documentElement.dataset.theme = theme;}, theme);
      for (const width of [1440, 768, 390, 320]) {
        await page.setViewportSize({width, height: 900});
        for (const scale of [80, 100, 120]) {
          await page.evaluate(scale => {document.documentElement.style.zoom = String(scale / 100); document.documentElement.style.setProperty('--page-scale', String(scale / 100));}, scale);
          await fit(`admin${path}/${theme}/${width}/${scale}`);
        }
      }
      }
      if (!baseline) {
        await page.evaluate(() => {document.documentElement.style.zoom = '1'; document.documentElement.style.setProperty('--page-scale', '1');});
        await page.setViewportSize({width: 1440, height: 900});
        await page.screenshot({path: `${artifacts}/admin-${path.split('/').pop()}-dark-1440.png`, fullPage: true, animations: 'disabled'});
      }
    }
    await login('empty_ui');
    for (const path of ['/teacher/tests', '/teacher/results', '/teacher/sessions', '/teacher/journal']) {
      assert.equal((await page.goto(base + path)).status(), 200);
      await fit('empty' + path);
    }
    checks.push('Administrator and empty teacher states');
    if (!baseline) {
      assert.equal(errors.length, 0, JSON.stringify(errors));
      assert.equal(externalRequests.length, 0, JSON.stringify(externalRequests));
      checks.push('All scripts and charts load locally without external network requests');
    }
  } finally {
    fs.writeFileSync(artifacts + '/checks.json', JSON.stringify({baseline, checks, layouts, errors, externalRequests}, null, 2));
    await browser.close();
  }
  console.log(JSON.stringify({baseline, layouts: layouts.length, overflow: layouts.filter(x => x.scroll > x.client + 2 || x.problems.length).length, errors, checks}));
})().catch(error => {console.error(error); process.exitCode = 1;});
