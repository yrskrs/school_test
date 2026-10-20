// Real Chromium + HTTP + PostgreSQL, only on the disposable compose.runtime project.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = 'http://app:8000';
const artifacts = process.env.AUDIT_ARTIFACTS || '/artifacts';

(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  const teacher = await browser.newContext();
  const errors = [], dialogs = [], checks = [], layouts = [];
  let activePage;
  fs.mkdirSync(artifacts, {recursive: true});
  await teacher.request.post(base + '/teacher/login', {form: {username: 'stability_check', password: 'synthetic-stability-password'}});
  async function pupil(name, code = 'STAB01') {
    const context = await browser.newContext({viewport: {width: 1366, height: 768}, acceptDownloads: true});
    const page = await context.newPage();
    activePage = page;
    page.on('pageerror', error => errors.push({name, message: error.message, stack: error.stack}));
    page.on('dialog', async dialog => {dialogs.push(dialog.message()); await dialog.dismiss();});
    await page.goto(base + '/student/login?code=' + code);
    if (code === 'STAB02') await page.locator('#roster_student_id').selectOption({index: 1});
    else await page.locator('#student_name').fill(name);
    await page.locator('#btn-submit-login').click();
    await page.waitForURL('**/student/instruction/*');
    return {context, page, id: Number(page.url().split('/').at(-1))};
  }
  async function start(student) {
    await student.page.locator('.btn-start').click();
    await student.page.getByRole('button', {name: 'Розпочати тестування', exact: true}).click();
    await student.page.waitForFunction(() => !!document.fullscreenElement && hasStarted);
  }
  async function choose(page) {await page.locator('.option-label').getByText('Правильно', {exact: true}).click();}
  async function answer(page) {
    await choose(page);
    const number = await page.locator('#current-q-num').innerText();
    await page.locator('#btn-next').click();
    await page.waitForFunction(number => document.getElementById('current-q-num').textContent !== number, number);
  }
  async function exit(page, count) {
    await page.evaluate(() => document.exitFullscreen());
    if (count < 3) {
      await page.waitForFunction(count => violations === count && isViolationShowing, count);
      await page.locator('#violation-return-btn').click();
      await page.waitForFunction(() => !!document.fullscreenElement && !isViolationShowing);
    }
  }
  async function stopped(student, warnings) {
    await student.page.waitForURL('**/finished', {timeout: 20000});
    await student.page.locator('#page-scale-controls').waitFor({state:'visible'});
    assert.equal(await student.page.locator('.finished-title').innerText(), 'Тест зупинено');
    const state = await (await teacher.request.get(base + '/api/attempt/' + student.id)).json();
    assert.equal(state.status, 'stopped');
    assert.equal(state.violation_count, warnings);
  }
  async function finish(student) {
    await student.page.locator('#btn-finish').click();
    await student.page.locator('#confirm-finish-btn').click();
    await student.page.waitForURL('**/finished');
  }
  async function copyAvailable(student) {
    await student.page.locator('#local-recovery').waitFor({state: 'visible'});
    const downloaded = student.page.waitForEvent('download');
    await student.page.locator('#download-local-recovery').click();
    assert.equal((await downloaded).suggestedFilename(), `schooltest-attempt-${student.id}-local.json`);
  }
  try {
    const roster = await pupil('', 'STAB02');
    for (const [width, height] of [[1366,768], [1280,720], [1024,600]]) {
      await roster.page.setViewportSize({width, height});
      const layout = await roster.page.evaluate(() => {
        const start = document.querySelector('.btn-start').getBoundingClientRect();
        const scale = document.getElementById('page-scale-controls').getBoundingClientRect();
        const theme = document.getElementById('floating-theme-btn').getBoundingClientRect();
        return {width: innerWidth, height: innerHeight, startBottom: start.bottom,
          scrollHeight: document.documentElement.scrollHeight, scaleTop: scale.top, themeTop: theme.top,
          scaleHeight: scale.height, themeHeight: theme.height};
      });
      layouts.push(layout);
      assert(layout.startBottom <= layout.height && layout.scrollHeight <= layout.height + 1, JSON.stringify(layout));
      assert(Math.abs(layout.scaleTop - layout.themeTop) < 1 && Math.abs(layout.scaleHeight - layout.themeHeight) < 1);
    }
    await roster.page.setViewportSize({width:1366,height:768});
    for (const [width,height] of [[1366,768],[1280,720]]) {
      await roster.page.setViewportSize({width,height});
      for (let n=0;n<2;n++) await roster.page.locator('[data-scale-action="increase"]').click();
      const bottom = await roster.page.locator('.btn-start').evaluate(el=>el.getBoundingClientRect().bottom);
      assert(bottom<=height,JSON.stringify({width,height,scale:120,startBottom:bottom}));
      assert(await roster.page.evaluate(()=>document.documentElement.scrollHeight<=innerHeight+1));
      layouts.push({width,height,scale:120,startBottom:bottom});
      await roster.page.locator('[data-scale-action="reset"]').click();
    }
    await roster.page.setViewportSize({width:1366,height:768});
    await roster.page.screenshot({path: artifacts + '/instruction_1366x768.png', animations:'disabled'});
    await roster.context.close();
    checks.push('Roster entry: compact instructions and start button fit without scrolling at 1366x768, 1280x720 and 1024x600; theme and scale align');
    console.log('PASS: compact instruction and aligned controls');

    const normal = await pupil('Три попередження');
    await start(normal);
    await answer(normal.page);
    await choose(normal.page);
    for (let n = 1; n <= 3; n++) await exit(normal.page, n);
    await stopped(normal, 3);
    assert((await normal.page.locator('.finished-subtitle').innerText()).includes('третього'));
    await normal.page.screenshot({path: artifacts + '/three_warnings_finished.png', animations:'disabled'});
    await normal.context.close();
    checks.push('Three real fullscreen exits with a saved answer and a draft: finished page, exactly three violations, no alert or endless overlay');
    console.log('PASS: third warning reaches finished page');

    const draft = await pupil('Чернетка перед зупинкою');
    await start(draft);
    await choose(draft.page);
    assert((await teacher.request.post(base + `/teacher/attempts/${draft.id}/stop`)).ok());
    await stopped(draft, 0);
    await copyAvailable(draft);
    await draft.context.close();
    checks.push('Teacher stop with an unsent draft opens result and offers a downloadable local copy');

    const rejected = await pupil('Відхилена чернетка');
    await rejected.page.routeWebSocket('**/ws/student/**', ws => ws.close());
    await rejected.page.route('**/save-answer', route => route.fulfill({status:422, json:{detail:'Synthetic rejected draft'}}));
    await rejected.page.route('**/student/event/*', async route => {
      const body = route.request().postDataJSON();
      if (body.event_type === 'tab_blur' && body.details.includes('3/3')) {
        await route.fetch(); // Commit the server stop, then lose its HTTP acknowledgement.
        await route.abort('internetdisconnected');
      } else await route.continue();
    });
    await start(rejected);
    await choose(rejected.page);
    for (let n=1; n<=3; n++) await exit(rejected.page,n);
    await stopped(rejected,3);
    await copyAvailable(rejected);
    await rejected.page.screenshot({path: artifacts + '/stopped_with_local_copy.png', animations:'disabled'});
    await rejected.context.close();
    checks.push('Rejected draft + lost third-stop HTTP acknowledgement + disconnected WebSocket: fallback reaches result and preserves local copy');
    console.log('PASS: rejected draft and lost stop acknowledgement recover');

    const offline = await pupil('Попередження офлайн');
    await start(offline);
    await choose(offline.page);
    for (let n=1; n<=2; n++) await exit(offline.page,n);
    await offline.context.setOffline(true);
    await offline.page.locator('#offline-block').waitFor({state:'visible'});
    await exit(offline.page,3);
    await offline.page.waitForFunction(() => violations === 3);
    assert.equal(await offline.page.evaluate(() => violations),3);
    assert.equal(await offline.page.evaluate(() => document.getElementById('question-card').inert),true);
    await offline.context.setOffline(false);
    await stopped(offline,3);
    await offline.context.close();
    checks.push('Third warning while offline blocks work locally; reconnect sends draft and three events, then opens stopped result');

    const network = await pupil('Відновлення мережі');
    await start(network);
    await choose(network.page);
    assert.equal(await network.page.locator('[data-connection-check]').count(), 0);
    await network.page.waitForFunction(() => document.getElementById('student-network-status').dataset.state === 'online');
    await network.context.setOffline(true);
    await network.page.locator('#offline-block').waitFor({state:'visible'});
    const timers = await network.page.evaluate(() => [timeLeft,qTimeLeft]);
    await network.page.waitForTimeout(3500); // Cross a scheduled automatic retry.
    assert.deepEqual(await network.page.evaluate(() => [timeLeft,qTimeLeft]),timers);
    assert.equal(await network.page.evaluate(() => document.getElementById('question-card').inert),true);
    await network.page.waitForFunction(() => document.getElementById('student-network-status').dataset.state === 'offline');
    assert.equal(await network.page.locator('#offline-block [data-network-text]').innerText(), 'Немає зв’язку');
    assert.equal(await network.page.locator('.network-status-dot').evaluate(el => getComputedStyle(el).animationName), 'network-pulse');
    await network.page.emulateMedia({reducedMotion:'reduce'});
    assert.equal(await network.page.locator('.network-status-dot').evaluate(el => getComputedStyle(el).animationName), 'none');
    await network.page.emulateMedia({reducedMotion:'no-preference'});
    await network.context.setOffline(false);
    await network.page.locator('#offline-block').waitFor({state:'hidden'});
    await network.page.waitForFunction(() => !isOffline && !document.getElementById('question-card').inert);
    assert.equal(await network.page.locator('.option-item input:checked').count(),1);
    // An HTTP outage without a browser offline event must also auto-recover.
    await network.page.route('**/api/attempt/*', route => route.fulfill({status:503, json:{detail:'Synthetic outage'}}));
    await network.page.locator('#offline-block').waitFor({state:'visible'});
    await network.page.unroute('**/api/attempt/*');
    await network.page.locator('#offline-block').waitFor({state:'hidden',timeout:15000});
    const acknowledgements = [];
    let committedAnswerCount = null;
    const networkState = await (await teacher.request.get(base + '/api/attempt/' + network.id)).json();
    await network.page.route('**/save-answer', async route => {
      const response = await route.fetch();
      acknowledgements.push((await response.json()).status);
      if (acknowledgements.length === 1) {
        const monitor = await (await teacher.request.get(base + '/api/session-status/' + networkState.session_id)).json();
        committedAnswerCount = monitor.attempts.find(a => a.id === network.id).assigned_questions.filter(q => q.is_correct !== null).length;
        await route.abort('internetdisconnected');
      } else await route.fulfill({response});
    });
    await choose(network.page);
    const beforeAck = await network.page.locator('#current-q-num').innerText();
    await network.page.locator('#btn-next').click();
    await network.page.locator('#offline-block').waitFor({state:'visible'});
    assert.equal(committedAnswerCount, 1, 'The teacher must see the committed DB answer before the student receives its acknowledgement');
    assert.equal(await network.page.evaluate(() => lockedQuestions.size), 0);
    assert.equal(await network.page.evaluate(() => Object.keys(pendingAnswers).length), 1);
    await network.page.waitForFunction(number => document.getElementById('current-q-num').textContent !== number, beforeAck);
    assert.deepEqual(acknowledgements, ['ok', 'already_saved']);
    assert.equal(await network.page.evaluate(() => lockedQuestions.size), 1);
    await network.page.unroute('**/save-answer');
    await finish(network);
    await network.context.close();
    checks.push('Topbar network indicator reports online/offline without manual buttons; offline freezes both timers and blocks editing; native reconnect and a 503 outage auto-recover with the draft intact');
    checks.push('A real PostgreSQL answer is visible to the teacher before a deliberately lost HTTP acknowledgement; automatic retry returns already_saved, confirms once and advances');
    console.log('PASS: network recovery, timers and automatic status');

    const paused = await pupil('Пауза й мережа');
    await start(paused);
    assert((await teacher.request.post(base + `/teacher/attempts/${paused.id}/pause`)).ok());
    await paused.page.locator('#pause-block').waitFor({state:'visible'});
    await paused.context.setOffline(true);
    await paused.page.locator('#offline-block').waitFor({state:'visible'});
    await paused.context.setOffline(false);
    await paused.page.locator('#offline-block').waitFor({state:'hidden'});
    assert.equal(await paused.page.evaluate(() => isPaused),true);
    assert.equal(await paused.page.evaluate(() => offlinePingInterval),null);
    assert((await teacher.request.post(base + `/teacher/attempts/${paused.id}/resume`)).ok());
    await paused.page.locator('#pause-block').waitFor({state:'hidden'});
    await answer(paused.page);
    await finish(paused);
    await paused.context.close();
    checks.push('Recovery preserves teacher pause and clears the retry interval; teacher resume and normal finish still work');
    assert.deepEqual(errors,[]);
    assert.deepEqual(dialogs,[]);
    fs.writeFileSync(artifacts + '/browser_stability.json', JSON.stringify({checks,layouts,javascript_errors:errors,native_dialogs:dialogs},null,2)+'\n');
    console.log(JSON.stringify({passed:checks.length,checks,javascript_errors:errors,native_dialogs:dialogs},null,2));
  } catch (error) {
    if (activePage && !activePage.isClosed()) {
      await activePage.screenshot({path: artifacts + '/failure.png', fullPage:true}).catch(()=>{});
      const state = await activePage.evaluate(() => ({url:location.href,offline:typeof isOffline==='undefined'?null:isOffline,
        finished:typeof testFinished==='undefined'?null:testFinished,events:typeof pendingEvents==='undefined'?null:pendingEvents.length})).catch(()=>({}));
      fs.writeFileSync(artifacts + '/failure.json',JSON.stringify({error:error.message,state,errors,layouts},null,2));
    }
    throw error;
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
