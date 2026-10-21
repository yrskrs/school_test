// Real HTTP/WS against the disposable schooltest-attempts-ui database only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || '/qa/node_modules/playwright');
const base = 'http://app:8000';
const output = '/artifacts';
(async () => {
  const browser = await chromium.launch({args:['--no-sandbox']});
  const context = await browser.newContext({viewport:{width:1366,height:900}});
  const page = await context.newPage();
  const errors=[],checks=[],layouts=[];
  context.on('page', p => p.on('pageerror', e=>errors.push(e.message)));
  page.on('pageerror',e=>errors.push(e.message));
  async function status() { return (await page.request.get(base+'/api/session-status/1')).json(); }
  async function refresh() { await page.evaluate(()=>fetchSessionStatus()); }
  async function activeFirst(p=page) {
    for(const selector of ['#students-tbody > tr','#monitor-mobile-cards > div']) {
      const rows = await p.locator(selector).evaluateAll(rows=>rows.filter(r=>!r.hidden).map(r=>({history:r.classList.contains('monitor-history-summary'),status:r.dataset.status})));
      let finished=false;
      for (const row of rows) {
        const done=row.history || ['finished','timeout','stopped'].includes(row.status);
        if(done) finished=true; else assert(!finished,JSON.stringify(rows));
      }
    }
  }
  async function fit(label) {
    await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
    const result=await page.evaluate(()=>{
      const bad=[];
      for(const button of document.querySelectorAll('main .btn')) {
        if(!button.checkVisibility())continue;
        const walker=document.createTreeWalker(button,NodeFilter.SHOW_TEXT);
        const tops=[];let node;
        while((node=walker.nextNode())) {
          if(!node.textContent.trim())continue;
          const range=document.createRange();range.selectNodeContents(node);
          tops.push(...[...range.getClientRects()].filter(r=>r.height>0).map(r=>Math.round(r.top)));
        }
        const lines=new Set(tops);
        // Header/filter buttons may wrap between words; row actions must stay readable.
        if(button.closest('.results-table,#students-table,.teacher-session-table') && lines.size>1) bad.push({text:button.textContent.trim(),lines:lines.size,width:button.getBoundingClientRect().width});
      }
      return {width:innerWidth,client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth,bad};
    });
    layouts.push({label,...result});
    assert(result.scroll<=result.client+2,JSON.stringify({label,...result}));
    assert.equal(result.bad.length,0,JSON.stringify({label,...result}));
  }
  try {
    await page.goto(base+'/teacher/login');
    await page.locator('#username').fill('teacher_ui');
    await page.locator('#password').fill('synthetic-ui-password');
    await Promise.all([page.waitForURL('**/teacher/dashboard'),page.locator('button[type=submit]').click()]);
    await page.goto(base+'/teacher/sessions/1/monitor');
    let data=await status();
    const active=data.attempts.find(a=>a.student_name==='Поточна третя спроба');
    assert(active, 'Seed the extra attempt-group scenarios');
    const groupKey=active.student_key;
    const legacy=data.attempts.filter(a=>a.student_name.toLocaleLowerCase('uk').replace(/\s+/g,' ').trim()==='іван іван');
    assert.equal(legacy.length,2);assert.equal(legacy[0].student_key,legacy[1].student_key);
    const group=page.locator('#history-'+groupKey);
    await group.waitFor();
    const rowCount=data.attempts.length;
    for(let i=0;i<15;i++) {
      await refresh();
      assert.equal(await page.locator('.monitor-attempt-row').count(),rowCount);
      for(const attempt of data.attempts) {
        assert.equal(await page.locator(`#row-${attempt.id} .monitor-attempt-number`).count(),attempt.session_attempt_count>1?1:0);
      }
    }
    await activeFirst();
    assert(await page.locator('#row-'+active.id).isVisible());
    await group.locator('button').click();
    await refresh();
    assert.equal(await group.locator('button').getAttribute('aria-expanded'),'true');
    assert(await page.locator('#row-1').isVisible());
    assert(await page.locator('#row-5').isVisible());
    const stop=await page.request.post(base+`/teacher/attempts/${active.id}/stop`);
    assert.equal(stop.status(),200);await refresh();
    assert.match(await group.locator('button').innerText(),/\(3\)/);
    const finishedCount=await page.locator('#finished-count').innerText();
    for(let i=0;i<3;i++) await page.evaluate(id=>handleWsEvent({event:'stop',attempt_id:id,status:'stopped',score:0,max_score:6}),active.id);
    assert.equal(await page.locator('#finished-count').innerText(),finishedCount);
    await activeFirst();
    await page.locator('#monitor-sort').selectOption('name');
    await page.reload();
    assert.equal(await page.locator('#monitor-sort').inputValue(),'name');
    for(const sort of ['newest','oldest','progress','status']) {await page.locator('#monitor-sort').selectOption(sort);await refresh();await activeFirst();}
    checks.push('15 refreshes keep one badge and one row per attempt; ID-based history; active-first sorting persists; completion groups immediately; duplicate WS events do not inflate counts');

    const live=await context.newPage();
    const sessions=await (await page.request.get(base+'/teacher/sessions')).text();
    assert(sessions.includes('GROUPS2'));
    await live.goto(base+'/teacher/sessions/3/monitor');
    await live.waitForFunction(()=>document.getElementById('ws-dot').classList.contains('connected'));
    const studentContext=await browser.newContext();
    const student=await studentContext.newPage();
    await student.goto(base+'/student/login?code=GROUPS2');
    await student.locator('#student_name').fill('Новий учень динамічно');
    await Promise.all([student.waitForURL('**/student/instruction/*'),student.locator('#btn-submit-login').click()]);
    const dynamic=await (await live.request.get(base+'/api/session-status/3')).json();
    const newId=dynamic.attempts[0].id;
    await live.locator('#row-'+newId).waitFor();
    await live.setViewportSize({width:390,height:844});
    assert(await live.locator('#mcard-'+newId).isVisible());
    assert.equal((await live.request.post(base+`/teacher/attempts/${newId}/delete`)).status(),200);
    await live.waitForFunction(id=>!document.getElementById('mcard-'+id),newId);
    assert.equal(await live.locator('#student-count').innerText(),'0');
    await studentContext.close();await live.close();
    checks.push('Real student login creates desktop row and mobile card through WS; deletion from another tab removes both without reload');

    for(const [label,path] of [['monitor','/teacher/sessions/1/monitor'],['results','/teacher/results'],['sessions','/teacher/sessions']]) {
      assert.equal((await page.goto(base+path)).status(),200);
      await page.evaluate(()=>document.fonts.ready);
      for(const theme of ['light','dark']) for(const width of [1440,1366,1024,800,768,390,320]) {
        await page.setViewportSize({width,height:900});
        for(const scale of [80,100,120]) {
          await page.evaluate(({theme,scale})=>{document.documentElement.dataset.theme=theme;document.documentElement.style.zoom=String(scale/100);document.documentElement.style.setProperty('--page-scale',String(scale/100));},{theme,scale});
          await fit(`${label}/${theme}/${width}/${scale}`);
        }
        if([1366,390].includes(width)) {
          await page.evaluate(()=>{document.documentElement.style.zoom='1';document.documentElement.style.setProperty('--page-scale','1');});
          await page.screenshot({path:`${output}/${label}-${theme}-${width}.png`,fullPage:true,animations:'disabled'});
        }
      }
    }
    await page.goto(base+'/teacher/results');
    const button=page.getByRole('button',{name:'Спроби (3)',exact:true});
    await button.click();assert.equal(await button.getAttribute('aria-expanded'),'true');
    await fit('expanded-results');
    await page.goto(base+'/teacher/sessions?sort=oldest');
    assert.equal(await page.locator('#session-sort').inputValue(),'oldest');
    assert.equal(await page.locator('.teacher-session-table th').nth(1).textContent(),'Код доступу');
    checks.push('Results expand completed attempts inline; session code/status have separate columns; all row buttons stay on one line across 126 layouts');
    assert.deepEqual(errors,[]);
  } catch(error) {
    await page.screenshot({path:output+'/failure.png',fullPage:true}).catch(()=>{});
    throw error;
  } finally {
    fs.writeFileSync(output+'/checks.json',JSON.stringify({checks,layouts,errors},null,2));
    await browser.close();
  }
  console.log(JSON.stringify({checks,layouts:layouts.length,errors}));
})().catch(e=>{console.error(e);process.exitCode=1;});
