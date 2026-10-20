// Exercise production pointer handlers at multiple CSS zoom levels on synthetic DOM.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = 'http://app:8000';
const artifacts = process.env.AUDIT_ARTIFACTS || '/artifacts';

(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  const page = await browser.newPage({viewport: {width: 1280, height: 900}});
  const errors = [], checks = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base + '/student/login?code=VIEW01');
    await page.evaluate(() => {
      Object.assign(window, {ATTEMPT_ID: 999999, SESSION_ID: 999999, TIME_REMAINING: null,
        ATTEMPT_STATUS: 'in_progress', SAVED_ANSWERS: {}, LOCKED_QUESTIONS: [], SKIPPED_QUESTIONS: [],
        TEST_DATA: {questions: [], time_limit_per_question: null}});
    });
    await page.addScriptTag({url: base + '/static/js/student-cache.js'});
    await page.addScriptTag({url: base + '/static/js/student.js'});
    await page.evaluate(() => {
      // These checks inspect pointer geometry without submitting synthetic IDs.
      logBrowserEvent = () => {};
      validateAnswer = () => {};
      renderQuestion = () => {};
    });
    for (const scale of [0.6, 0.8, 1, 1.5]) {
      await page.evaluate(scale => {
        document.documentElement.style.zoom = String(scale);
        document.querySelector('.main-content').innerHTML = '<div id="geometry" style="width:600px;max-width:100%"></div>';
        const image = 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"><rect width="200" height="100" fill="lightblue"/></svg>');
        document.getElementById('geometry').innerHTML = buildQuestionHTML({id: 901, question_type: 'hotspot',
          question_text: 'Область', image_url: image, points: 1, options: []}, 0);
      }, scale);
      await page.locator('#hotspot-img-901').waitFor();
      await page.waitForFunction(() => document.getElementById('hotspot-img-901').naturalWidth === 200);
      const image = await page.locator('#hotspot-img-901').boundingBox();
      await page.mouse.click(image.x + image.width * .5, image.y + image.height * .5);
      const result = await page.evaluate(() => {
        restoreHotspotMarker(901);
        const marker = document.getElementById('hotspot-marker-901').getBoundingClientRect();
        return {value: answers[901], x: marker.x + marker.width / 2, y: marker.y + marker.height / 2};
      });
      assert(Math.abs(result.value.x - 100) < 2 && Math.abs(result.value.y - 50) < 2);
      assert(Math.abs(result.x - (image.x + image.width / 2)) < 2);
      assert(Math.abs(result.y - (image.y + image.height / 2)) < 2);

      for (const kind of ['sequence', 'matching']) {
        await page.evaluate(kind => {
          const q = {id: 902, question_type: kind, question_text: 'Перетягування', points: 1,
            matching_pool: ['Права А', 'Права Б'], options: [
              {id: 903, option_text: 'Ліва А', matching_text: 'Права А'},
              {id: 904, option_text: 'Ліва Б', matching_text: 'Права Б'}]};
          questions.splice(0, questions.length, q);
          sequenceOrders[902] = [903, 904];
          matchingPools[902] = q.matching_pool;
          answers[902] = kind === 'sequence' ? [903, 904] : {};
          document.getElementById('geometry').innerHTML = buildQuestionHTML(q, 0);
        }, kind);
        const selector = kind === 'sequence' ? '.draggable-sequence-item' : '.matching-draggable-card';
        const card = await page.locator(selector).first().boundingBox();
        const x = card.x + card.width / 2, y = card.y + card.height / 2;
        await page.mouse.move(x, y);
        await page.mouse.down();
        await page.mouse.move(x + 30, y + 35, {steps: 5});
        const ghost = await page.locator(kind === 'sequence' ? '.sequence-drag-ghost' : '.matching-drag-ghost').boundingBox();
        assert(ghost && Math.abs(ghost.width - card.width) < Math.max(15, card.width * .07), JSON.stringify({kind, scale, card, ghost}));
        assert(Math.abs((ghost.x + ghost.width / 2) - (x + 30)) < 10, `${kind} ghost x at ${scale}`);
        assert(Math.abs((ghost.y + ghost.height / 2) - (y + 35)) < 10, `${kind} ghost y at ${scale}`);
        await page.mouse.up();
      }
      checks.push(`Hotspot coordinates/marker and both drag ghosts at ${scale * 100}%`);
    }
    // Use the production teacher SVG editor helpers too.
    await page.goto(base + '/student/login?code=VIEW01');
    await page.evaluate(() => {
      document.querySelector('.main-content').innerHTML = '<div id="geometry"></div>';
    });
    await page.addScriptTag({url: base + '/static/js/teacher.js'});
    await page.evaluate(() => {setHotspotAnswer = () => {};});
    for (const scale of [.6, .8, 1, 1.5]) {
      await page.evaluate(scale => {
        document.documentElement.style.zoom = String(scale);
        const image = 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"><rect width="200" height="100" fill="lightblue"/></svg>');
        document.getElementById('geometry').innerHTML = `<div style="position:relative;width:200px;height:100px">
          <img id="hotspot-editor-img-905" src="${image}" style="width:100%;height:100%">
          <svg id="hotspot-editor-svg-905" style="position:absolute;inset:0;width:100%;height:100%">
            <polygon id="hotspot-poly-905"></polygon><rect id="hotspot-drag-rect-905"></rect></svg></div>
          <input id="hotspot-input-905" value="">`;
      }, scale);
      await page.waitForFunction(() => document.getElementById('hotspot-editor-img-905').naturalWidth === 200);
      await page.evaluate(() => initHotspotEditor(905));
      const svg = await page.locator('#hotspot-editor-svg-905').boundingBox();
      await page.mouse.move(svg.x + svg.width * .25, svg.y + svg.height * .25);
      await page.mouse.down();
      await page.mouse.move(svg.x + svg.width * .75, svg.y + svg.height * .75, {steps: 5});
      await page.mouse.up();
      assert.equal(await page.locator('#hotspot-input-905').inputValue(), '(50,25)-(150,25)-(150,75)-(50,75)');
      const poly = await page.locator('#hotspot-poly-905').boundingBox();
      assert(Math.abs(poly.width - svg.width / 2) < 2);
      assert(Math.abs(poly.height - svg.height / 2) < 2);
      checks.push(`Teacher hotspot editor selection/polygon at ${scale * 100}%`);
    }
    assert.deepEqual(errors, []);
    fs.writeFileSync(artifacts + '/coordinates_checks.json', JSON.stringify({checks, javascript_errors: errors}, null, 2) + '\n');
    console.log(JSON.stringify({checks, javascript_errors: errors}, null, 2));
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
