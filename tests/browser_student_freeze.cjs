// Real browser/HTTP/PostgreSQL. Only tests/compose.runtime.yaml synthetic data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = 'http://app:8000';
const artifacts = process.env.AUDIT_ARTIFACTS || '/artifacts';

(async () => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  const errors = [], checks = [];
  let activePage;
  fs.mkdirSync(artifacts,{recursive:true});
  async function pupil(name) {
    const context = await browser.newContext({viewport:{width:1366,height:768}});
    const page = await context.newPage(); activePage = page;
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(base+'/student/login?code=FREEZE01');
    await page.locator('#student_name').fill(name);
    await page.locator('#btn-submit-login').click();
    await page.waitForURL('**/student/instruction/*');
    await page.locator('.btn-start').click();
    await page.getByRole('button',{name:'Розпочати тестування',exact:true}).click();
    await page.waitForFunction(()=>hasStarted && !!document.fullscreenElement);
    await page.waitForFunction(()=>document.querySelector('#question-card img')?.naturalWidth===826);
    return {page,context,id:Number(page.url().split('/').at(-1))};
  }
  async function finish(page) {
    await page.locator('#btn-finish').click();
    await page.locator('#confirm-finish-btn').click();
    await page.waitForURL('**/finished');
  }
  async function selectImage(page) {
    const image = page.locator('#question-card img');
    await image.click();
    assert.equal(await page.evaluate(()=>Number.isFinite(answers[questions[currentIndex].id].x)),true);
  }
  async function poison(page) {
    // A malformed old cache or a browser-rounded edge click must remain recoverable.
    await page.evaluate(()=>{ answers[questions[currentIndex].id]={x:-0.25,y:50}; validateAnswer(); });
  }
  async function reject(page) {
    await poison(page);
    const response = page.waitForResponse(r=>r.url().endsWith('/save-answer') && r.status()===422);
    await page.locator('#btn-next').click(); await response;
    await page.waitForFunction(()=>!isSaving);
  }
  async function save(page) {
    const before = await page.locator('#current-q-num').innerText();
    await page.locator('#btn-next').click();
    await page.waitForFunction(number=>document.getElementById('current-q-num').textContent!==number
      || document.getElementById('finish-modal').style.display==='flex',before);
  }
  try {
    if (process.env.FREEZE_BASELINE) {
      const baseline = await pupil('Базове відхилення '+Date.now());
      await reject(baseline.page);
      assert.equal(await baseline.page.evaluate(()=>canEditQuestion(questions[currentIndex].id)),false);
      await baseline.page.locator('#btn-finish').click();
      await baseline.page.locator('#confirm-finish-btn').click();
      await baseline.page.waitForFunction(()=>!finishInProgress);
      assert.equal(await baseline.page.evaluate(()=>!!finishIntent && !canEditQuestion(questions[currentIndex].id)),true);
      await baseline.page.screenshot({path:artifacts+'/before_rejected_freeze.png',animations:'disabled'});
      fs.writeFileSync(artifacts+'/baseline.json',JSON.stringify({version:'1.6.10',http:422,editing_blocked:true,finish_intent_stuck:true},null,2)+'\n');
      console.log('REPRODUCED: 1.6.10 freezes editing and completion after a rejected image coordinate');
      await baseline.context.close();
      return;
    }

    const mixed = await pupil('Вісім типів');
    for (const scale of [80,100,120]) {
      await mixed.page.locator('[data-scale-action="reset"]').click();
      for(let i=100;i!==scale;i+=scale<100?-10:10) await mixed.page.locator(`[data-scale-action="${scale<100?'decrease':'increase'}"]`).click();
      const image = mixed.page.locator('#question-card img');
      const box = await image.boundingBox();
      await mixed.page.mouse.click(box.x+1,box.y+1);
      await mixed.page.evaluate(()=>{
        const img=document.querySelector('#question-card img'), rect=img.getBoundingClientRect();
        img.dispatchEvent(new MouseEvent('click',{bubbles:true,clientX:rect.left-0.25,clientY:rect.top-0.25}));
      });
      const point=await mixed.page.evaluate(()=>answers[questions[currentIndex].id]);
      assert(point.x>=0 && point.y>=0 && point.x<=826 && point.y<=193);
      await selectImage(mixed.page);
    }
    await save(mixed.page);
    for (const kind of ['matching','multiple_choice','single_choice','short_text','sequence','image_choice','true_false']) {
      assert.equal(await mixed.page.evaluate(()=>questions[currentIndex].question_type),kind);
      if(kind==='matching') {
        for(let i=1;i<=3;i++) {
          await mixed.page.locator('.matching-pool .matching-draggable-card').filter({hasText:'Праворуч '+i}).click();
          await mixed.page.locator('.matching-row-student').filter({hasText:'Ліворуч '+i}).locator('.matching-drop-zone').click();
        }
      } else if(kind==='multiple_choice') {
        await mixed.page.locator('.option-item').nth(0).click();
        await mixed.page.locator('.option-item').nth(1).click();
      } else if(kind==='short_text') await mixed.page.locator('.short-text-input').fill('Відповідь');
      else if(kind==='sequence') await mixed.page.locator('.draggable-sequence-item').first().getByRole('button',{name:'▼'}).click();
      else await mixed.page.locator('.option-item').first().click();
      await save(mixed.page);
    }
    await mixed.page.locator('#confirm-finish-btn').click();
    await mixed.page.waitForURL('**/finished');
    await mixed.context.close();
    checks.push('All eight types save through real HTTP; 32-bit BMP at 80/100/120% and rounded edge coordinates stay finite and inside the image; normal completion works');

    const corrected = await pupil('Виправлена область');
    await reject(corrected.page);
    assert.equal(await corrected.page.evaluate(()=>canEditQuestion(questions[currentIndex].id)),true);
    assert.equal(await corrected.page.evaluate(()=>Object.keys(pendingAnswers).length),0);
    await corrected.page.locator('#answer-error').waitFor({state:'visible'});
    await corrected.page.screenshot({path:artifacts+'/rejected_image_editable.png',animations:'disabled'});
    await selectImage(corrected.page); await save(corrected.page); await finish(corrected.page);
    await corrected.context.close();
    checks.push('Real server 422 releases editing; a new image click replaces the rejected payload, saves and completes');

    const discarded = await pupil('Скинута область');
    await poison(discarded.page);
    await discarded.page.locator('#btn-finish').click();
    await discarded.page.locator('#confirm-finish-btn').click();
    await discarded.page.waitForFunction(()=>!finishInProgress && !finishIntent);
    assert.equal(await discarded.page.evaluate(()=>document.getElementById('question-card').inert),false);
    assert.equal(await discarded.page.locator('#finish-modal').isVisible(),false);
    await discarded.page.getByRole('button',{name:'Скинути незбережену відповідь',exact:true}).click();
    await finish(discarded.page); await discarded.context.close();
    checks.push('Rejected draft during finish cancels the stuck finish intent, opens the problem question and permits explicit discard and completion');

    const recovery = await pupil('Відновлення відхилення');
    await recovery.page.evaluate(()=>{
      const id=questions[currentIndex].id;
      answers[id]={x:null,y:50}; pendingAnswers[id]={question_id:id,answer_text:null,selected_options:answers[id]}; persistAttempt();
    });
    await recovery.context.setOffline(true);
    await recovery.page.locator('#offline-block').waitFor({state:'visible'});
    await recovery.context.setOffline(false);
    await recovery.page.waitForFunction(()=>!isOffline && Object.keys(pendingAnswers).length===0);
    assert.equal(await recovery.page.locator('#offline-block').isVisible(),false);
    assert.equal(await recovery.page.evaluate(()=>canEditQuestion(questions[currentIndex].id)),true);
    await selectImage(recovery.page); await save(recovery.page); await finish(recovery.page);
    await recovery.context.close();
    checks.push('Reconnect with an invalid old frozen payload does not leave an offline overlay; correcting and completing remains possible');

    const lost = await pupil('Втрачений відгук області');
    const statuses=[];
    await lost.page.route('**/save-answer',async route=>{
      const response=await route.fetch(); statuses.push((await response.json()).status);
      if(statuses.length===1) await route.abort('internetdisconnected');
      else await route.fulfill({response});
    });
    await selectImage(lost.page); await save(lost.page);
    assert.deepEqual(statuses,['ok','already_saved']);
    assert.equal(await lost.page.evaluate(()=>lockedQuestions.size),1);
    await finish(lost.page); await lost.context.close();
    checks.push('Lost acknowledgement still freezes the unknown delivery and retries the identical hotspot payload exactly once with already_saved');

    const slow = await pupil('Повільне тіло відповіді');
    await slow.page.evaluate(()=>{
      const original=window.fetch;
      window.slowRequestKind='status';
      window.fetch=(url,options)=>original(window.slowRequestKind &&
        (window.slowRequestKind==='status'?String(url).includes('/api/attempt/'):String(url).endsWith('/save-answer'))
          ?'http://slow-body:8765/delayed':url,options);
    });
    await slow.page.locator('#offline-block').waitFor({state:'visible',timeout:18000});
    assert.equal(await slow.page.evaluate(()=>recoveringConnection),false);
    await slow.page.evaluate(()=>{window.slowRequestKind=null;});
    await slow.page.locator('#offline-block').waitFor({state:'hidden',timeout:15000});
    await selectImage(slow.page);
    await slow.page.evaluate(()=>{window.slowRequestKind='answer';});
    await slow.page.locator('#btn-next').click();
    await slow.page.locator('#offline-block').waitFor({state:'visible',timeout:15000});
    assert.equal(await slow.page.evaluate(()=>isSaving),false);
    await slow.page.evaluate(()=>{window.slowRequestKind=null;});
    await slow.page.waitForFunction(()=>lockedQuestions.size===1 && !isOffline);
    await finish(slow.page); await slow.context.close();
    checks.push('Real HTTP headers arrive immediately but body is delayed 30s: both status and save requests abort at the body deadline, release flags, reconnect and complete');

    assert.deepEqual(errors,[]);
    fs.writeFileSync(artifacts+'/freeze_checks.json',JSON.stringify({checks,javascript_errors:errors},null,2)+'\n');
    console.log(JSON.stringify({passed:checks.length,checks,javascript_errors:errors},null,2));
  } catch(error) {
    if(activePage && !activePage.isClosed()) await activePage.screenshot({path:artifacts+'/failure.png',fullPage:true}).catch(()=>{});
    throw error;
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
