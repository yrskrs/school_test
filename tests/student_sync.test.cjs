/* Run with Node.js: node tests/student_sync.test.cjs */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const cacheCode = fs.readFileSync(path.join(root, 'app/static/js/student-cache.js'), 'utf8');
const studentCode = fs.readFileSync(path.join(root, 'app/static/js/student.js'), 'utf8');
const values = new Map();
const storage = {getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key)};
function environment(fetcher = async () => ({ok: true, json: async () => ({status: 'in_progress'})}), customStorage = storage) {
  const elements = new Map();
  const listeners = {};
  const intervals = new Map();
  const windowListeners = {};
  const dispatch = (name, event = {}) => (listeners[name] || []).forEach(fn => fn(event));
  const dispatchWindow = (name, event = {}) => (windowListeners[name] || []).forEach(fn => fn(event));
  let timerId = 0;
  const element = () => ({style: {}, dataset: {}, classList: {add() {}, remove() {}, contains() {return false;}},
    querySelector() {return this.child ||= element();}, appendChild() {}, addEventListener(name, fn) {(this.handlers ||= {})[name] = fn;}, textContent: '', disabled: false});
  const document = {getElementById(id) {if (!elements.has(id)) elements.set(id, element()); return elements.get(id);},
    addEventListener(name, fn) {(listeners[name] ||= []).push(fn);}, createElement: element,
    querySelectorAll: () => [], documentElement: {}, hidden: false, fullscreenElement: null};
  document.documentElement.requestFullscreen = async () => {document.fullscreenElement = document.documentElement; dispatch('fullscreenchange');};
  const context = vm.createContext({document, localStorage: customStorage, addEventListener(name, fn) {(windowListeners[name] ||= []).push(fn);},
    ATTEMPT_ID: 42, SESSION_ID: 1, TIME_REMAINING: 120, ATTEMPT_STATUS: 'in_progress',
    TEST_DATA: {questions: [{id: 1, question_type: 'single_choice', question_text: 'Q', options: [{id: 2, option_text: 'A'}]}], time_limit_per_question: 10},
    SAVED_ANSWERS: {}, LOCKED_QUESTIONS: [], SKIPPED_QUESTIONS: [], VIOLATION_COUNT: 0,
    location: {href: '', protocol: 'http:', host: 'local'}, AbortController, fetch: fetcher,
    WebSocket: Object.assign(class {constructor() {this.readyState = 1;}}, {OPEN: 1, CONNECTING: 0}),
    setTimeout: () => ++timerId, clearTimeout() {}, setInterval: fn => {intervals.set(++timerId, fn); return timerId;},
    clearInterval: id => intervals.delete(id), alert() {}, console});
  context.window = context;
  vm.runInContext(cacheCode, context);
  vm.runInContext(studentCode, context);
  return {context, elements, listeners, intervals, dispatch, dispatchWindow, run: code => vm.runInContext(code, context)};
}
async function runTests() {
  for (const file of ['student-cache.js', 'student.js', 'student-recovery.js', 'teacher-lists.js', 'test-import.js', 'teacher.js', 'monitor.js']) {
    new vm.Script(fs.readFileSync(path.join(root, 'app/static/js', file), 'utf8'), {filename: file});
  }
  const env = environment();
  for (const kind of ['single_choice', 'multiple_choice', 'matching', 'sequence']) {
    const html = env.run(`buildQuestionHTML({id: 100, question_type: '${kind}', question_text: 'Imported image',
      options: [{id: 101, option_text: 'Image', image_url: '/static/tests/synthetic/image.bmp'}]}, 0)`);
    assert.match(html, /src="\/static\/tests\/synthetic\/image.bmp"/, kind);
  }
  const first = env.context.StudentCache.create(42, storage);
  first.save({answers: {1: [2]}, violations: 2, questionTimes: {1: 3}});
  const loaded = env.context.StudentCache.create(42, storage);
  assert.equal(JSON.stringify(loaded.state.answers), '{"1":[2]}');
  assert.equal(loaded.state.violations, 2);
  assert.equal(loaded.state.questionTimes[1], 3);
  storage.setItem('unrelated', 'keep');
  env.context.StudentCache.create(43, storage).save({answers: {3: 'other attempt'}});
  loaded.clear();
  assert.equal(storage.getItem('unrelated'), 'keep');
  assert.notEqual(storage.getItem('schooltest:attempt:43:v1'), null);
  storage.setItem('schooltest:attempt:42:v1', '{invalid');
  assert.equal(env.context.StudentCache.create(42, storage).available, false);
  storage.setItem('schooltest:attempt:42:v1', '{"version":1,"answers":[],"pending":{},"events":[]}');
  assert.equal(JSON.stringify(env.context.StudentCache.create(42, storage).state.answers), '{}');
  storage.removeItem('schooltest:attempt:42:v1');
  const failing = env.context.StudentCache.create(99, {getItem() {return null;}, setItem() {throw Error('quota');}});
  assert.equal(failing.save({answers: {1: 'durable only in memory'}}), false);
  assert.equal(failing.state.answers[1], 'durable only in memory');

  let serverAnswer = null;
  let dropAcknowledgement = true;
  const requests = [];
  const fetcher = async (url, options = {}) => {
    requests.push(url);
    if (url.endsWith('save-answer')) {
      const submission = JSON.parse(options.body);
      if (serverAnswer) assert.equal(JSON.stringify(serverAnswer), JSON.stringify(submission));
      serverAnswer = submission;
      if (dropAcknowledgement) {dropAcknowledgement = false; throw Error('lost acknowledgement');}
      return {ok: true, json: async () => ({status: 'already_saved'})};
    }
    return {ok: true, json: async () => ({status: 'in_progress'})};
  };
  const offline = environment(fetcher);
  offline.run('answers[1] = [2]');
  assert.equal(await offline.run('saveCurrentAnswer()'), false);
  assert.equal(offline.run('isOffline'), true);
  assert.equal(JSON.parse(storage.getItem('schooltest:attempt:42:v1')).pending[1].selected_options[0], 2);
  assert.equal(offline.run('lockedQuestions.has(1)'), false);
  // Reload after a server write whose response was lost: same frozen request survives.
  const reload = environment(fetcher);
  assert.equal(reload.run('pendingAnswers[1].selected_options[0]'), 2);
  assert.equal(await reload.run('flushPendingAnswers()'), true);
  assert.equal(reload.run('lockedQuestions.has(1)'), true);
  assert.equal(reload.run('Object.keys(pendingAnswers).length'), 0);
  assert.equal(requests.filter(url => url.endsWith('save-answer')).length, 2);

  storage.removeItem('schooltest:attempt:42:v1');
  let resolveSave;
  let calls = 0;
  const simultaneous = environment(async () => {calls++; return new Promise(resolve => {resolveSave = resolve;});});
  const one = simultaneous.run('saveAnswer(1, null, [2])');
  const two = simultaneous.run('saveAnswer(1, null, [2])');
  assert.equal(calls, 1);
  resolveSave({ok: true});
  assert.equal(await one, true);
  assert.equal(await two, true);

  storage.removeItem('schooltest:attempt:42:v1');
  const rejected = environment(async () => ({ok: false, status: 422, json: async () => ({detail: 'Invalid answer'})}));
  assert.equal(await rejected.run('saveAnswer(1, null, [999])'), false);
  assert.equal(rejected.run('lockedQuestions.has(1)'), false);
  assert.equal(rejected.run('pendingAnswers[1].selected_options[0]'), 999);
  rejected.run('hasStarted = true; resetQTimer(); qTimeLeft = 1; questionQueue = [0];');
  const questionTimer = rejected.intervals.get(rejected.run('qTimerInterval'));
  await questionTimer();
  assert.equal(rejected.run('questionQueue.length'), 1, 'Rejected timer answer must not advance');

  storage.removeItem('schooltest:attempt:42:v1');
  const violations = environment();
  violations.run('handleViolation()');
  assert.equal(violations.run('violations'), 0, 'Entry overlay is not a violation');
  violations.run('hasStarted = true; handleViolation("fullscreen_exit"); handleViolation("tab_blur")');
  assert.equal(violations.run('violations'), 1, 'Blur and fullscreen events count once per warning');
  assert.equal(JSON.parse(storage.getItem('schooltest:attempt:42:v1')).violations, 1);
  violations.run('isViolationShowing = false; isPaused = true; handleViolation()');
  assert.equal(violations.run('violations'), 1, 'Teacher pause must not add a violation');

  storage.removeItem('schooltest:attempt:42:v1');
  const finishRequests = [];
  const finish = environment(async url => {
    finishRequests.push(url);
    return {ok: true, json: async () => ({status: 'ok'})};
  });
  finish.run('answers[1] = [2]');
  await finish.run('confirmFinish()');
  assert.equal(finishRequests[0], '/student/test/42/save-answer');
  assert.equal(finishRequests.at(-1), '/student/test/42/finish');
  assert.equal(storage.getItem('schooltest:attempt:42:v1'), null);
  assert.equal(finish.context.location.href, '/student/test/42/finished');
  storage.removeItem('schooltest:attempt:42:v1');
  let finishReachedServer = false;
  const lostFinish = environment(async url => {
    if (url.endsWith('/finish')) {finishReachedServer = true; throw Error('lost finish response');}
    return {ok: true, json: async () => ({status: finishReachedServer ? 'finished' : 'in_progress'})};
  });
  lostFinish.run('answers[1] = [2]');
  await lostFinish.run('confirmFinish()');
  assert.equal(lostFinish.run('testFinished'), false);
  assert.equal(JSON.parse(storage.getItem('schooltest:attempt:42:v1')).finish.timeout, false);
  await lostFinish.run('resumeConnection()');
  assert.equal(lostFinish.run('testFinished'), true);
  assert.equal(storage.getItem('schooltest:attempt:42:v1'), null);

  storage.removeItem('schooltest:attempt:42:v1');
  const stopped = environment(async url => url.includes('/api/')
    ? {ok: true, json: async () => ({status: 'stopped'})}
    : {ok: false, status: 409, json: async () => ({detail: 'Stopped'})});
  stopped.run('pendingAnswers[1] = {question_id: 1, answer_text: null, selected_options: [2]}; persistAttempt();');
  await stopped.run('resumeConnection()');
  assert.equal(stopped.run('testFinished'), false);
  assert.notEqual(storage.getItem('schooltest:attempt:42:v1'), null, 'Teacher stop must preserve an unconfirmed answer');

  storage.removeItem('schooltest:attempt:42:v1');
  const timers = environment();
  timers.run('startTimer()');
  const globalTick = timers.intervals.get(timers.run('timerInterval'));
  globalTick();
  assert.equal(timers.run('timeLeft'), 120);
  timers.run('hasStarted = true; isPaused = true'); globalTick();
  assert.equal(timers.run('timeLeft'), 120);
  timers.run('isPaused = false; isOffline = true'); globalTick();
  assert.equal(timers.run('timeLeft'), 120);
  timers.run('isOffline = false'); globalTick();
  assert.equal(timers.run('timeLeft'), 119);
  timers.run('questionTimes[1] = 3; resetQTimer(); resetQTimer()');
  assert.equal(timers.run('qTimeLeft'), 3);
  timers.run('isViolationShowing = true');
  timers.elements.get('violation-block') || timers.context.document.getElementById('violation-block');
  timers.elements.get('violation-block').style.display = 'flex';
  timers.context.document.documentElement.requestFullscreen = async () => {throw Error('denied');};
  await timers.run('returnToFullscreen()');
  assert.equal(timers.elements.get('violation-block').style.display, 'flex');
  assert.equal(timers.run('isViolationShowing'), true);

  storage.removeItem('schooltest:attempt:42:v1');
  const submittedDrafts = [];
  const skippedDraft = environment(async (url, options = {}) => {
    if (url.endsWith('save-answer')) submittedDrafts.push(JSON.parse(options.body).question_id);
    return {ok: true};
  });
  skippedDraft.run("questions.push({id: 3, question_type: 'short_text', options: []}); answers[1] = [2]; answers[3] = 'Skipped draft'; skippedQuestions.add(3)");
  await skippedDraft.run('confirmFinish()');
  assert.equal(JSON.stringify(submittedDrafts), '[1,3]', 'Finish must include drafted answers on skipped questions');
  skippedDraft.run("logBrowserEvent('tab_focus'); persistAttempt()");
  assert.equal(storage.getItem('schooltest:attempt:42:v1'), null, 'Late events must not recreate completed cache');

  storage.removeItem('schooltest:attempt:42:v1');
  const recovery = environment();
  recovery.context.StudentCache.create(42, storage).save({answers: {3: 'Unsynchronized draft'}});
  recovery.context.document.getElementById('recovery-config').textContent = JSON.stringify({attempt_id: 42, confirmed_ids: [1]});
  const recoveryCode = fs.readFileSync(path.join(root, 'app/static/js/student-recovery.js'), 'utf8');
  vm.runInContext(recoveryCode, recovery.context);
  assert.equal(recovery.elements.get('local-recovery').hidden, false);
  assert.notEqual(storage.getItem('schooltest:attempt:42:v1'), null);
  recovery.context.document.getElementById('recovery-config').textContent = JSON.stringify({attempt_id: 42, confirmed_ids: [1, 3]});
  vm.runInContext(recoveryCode, recovery.context);
  assert.equal(storage.getItem('schooltest:attempt:42:v1'), null);

  storage.removeItem('schooltest:attempt:42:v1');
  const fullscreenRequests = [];
  let serverViolations = 0;
  const fullscreen = environment(async (url, options = {}) => {
    fullscreenRequests.push({url, body: options.body && JSON.parse(options.body)});
    if (url.startsWith('/student/event/')) {
      const body = JSON.parse(options.body);
      if (body.event_type === 'tab_blur') serverViolations++;
      return {ok: true, json: async () => ({attempt_status: serverViolations >= 3 ? 'stopped' : 'in_progress', stop_reason: 'violations'})};
    }
    return {ok: true, json: async () => ({status: serverViolations >= 3 ? 'stopped' : 'in_progress', stop_reason: 'violations'})};
  });
  fullscreen.run('setupBrowserEvents(); answers[1] = [2];');
  await fullscreen.run('startFullscreenTest()');
  assert.equal(fullscreen.run('hasStarted'), true);
  for (let exit = 1; exit <= 3; exit++) {
    fullscreen.context.document.fullscreenElement = null;
    fullscreen.dispatch('fullscreenchange'); // Browser consumes Esc: no keydown.
    fullscreen.dispatch('webkitfullscreenchange'); // Alias must not double count.
    fullscreen.dispatchWindow('blur');
    assert.equal(fullscreen.run('violations'), exit);
    if (exit < 3) {
      await fullscreen.run('flushBrowserEvents()');
      assert.equal(fullscreen.elements.get('violation-block').style.display, 'flex');
      if (exit === 1) await fullscreen.run('returnToFullscreen()');
      else { // Returning by an external fullscreen control must reset the warning too.
        fullscreen.context.document.fullscreenElement = fullscreen.context.document.documentElement;
        fullscreen.dispatch('fullscreenchange');
      }
      assert.equal(fullscreen.run('isViolationShowing'), false);
    }
  }
  for (let n = 0; n < 30; n++) await Promise.resolve();
  await fullscreen.run('flushBrowserEvents()');
  assert.equal(serverViolations, 3);
  assert.equal(fullscreen.run('testFinished'), true);
  assert.equal(fullscreen.elements.get('violation-return-btn').style.display, 'none');
  assert.equal(fullscreen.elements.get('violation-return-timer').style.display, 'none');
  assert.equal(fullscreen.context.location.href, '/student/test/42/finished');
  const thirdEventIndex = fullscreenRequests.findLastIndex(r => r.body?.event_type === 'tab_blur');
  assert.ok(fullscreenRequests.findIndex(r => r.url.endsWith('save-answer')) < thirdEventIndex);
  assert.equal(fullscreenRequests.some(r => r.url.endsWith('/finish')), false, 'Violations must stop, not finish normally');

  storage.removeItem('schooltest:attempt:42:v1');
  const offlineExits = environment(async () => {throw Error('offline');});
  await offlineExits.run('startFullscreenTest()');
  offlineExits.run('isOffline = true');
  for (let n = 0; n < 3; n++) {
    offlineExits.context.document.fullscreenElement = null;
    offlineExits.dispatch('fullscreenchange');
    if (n < 2) await offlineExits.run('returnToFullscreen()');
  }
  assert.equal(offlineExits.run('violations'), 3);
  const stoppedCache = JSON.parse(storage.getItem('schooltest:attempt:42:v1'));
  assert.equal(stoppedCache.finish.reason, 'violations');
  assert.equal(stoppedCache.events.filter(e => e.event_type === 'tab_blur').length, 3);
  await offlineExits.run('flushBrowserEvents()');
  let deliveredExits = 0;
  const deliveredIds = new Set();
  const afterOfflineReload = environment(async (url, options = {}) => {
    if (url.startsWith('/student/event/')) {
      const event = JSON.parse(options.body);
      if (event.event_type === 'tab_blur' && !deliveredIds.has(event.client_event_id)) {
        deliveredExits++; deliveredIds.add(event.client_event_id);
      }
      return {ok: true, json: async () => ({attempt_status: deliveredExits >= 3 ? 'stopped' : 'in_progress', stop_reason: 'violations'})};
    }
    return {ok: true, json: async () => ({status: deliveredExits >= 3 ? 'stopped' : 'in_progress'})};
  });
  assert.equal(afterOfflineReload.run('finishIntent.reason'), 'violations');
  assert.equal(afterOfflineReload.run('violations'), 3);
  await afterOfflineReload.run('resumeConnection()');
  assert.equal(deliveredExits, 3, 'Offline exits reach the server after reload');
  assert.equal(afterOfflineReload.run('testFinished'), true);
  assert.equal(storage.getItem('schooltest:attempt:42:v1'), null);

  storage.removeItem('schooltest:attempt:42:v1');
  const missedEvent = environment();
  await missedEvent.run('startFullscreenTest()');
  missedEvent.context.document.fullscreenElement = null;
  missedEvent.run('checkFullscreenState(); checkFullscreenState();');
  assert.equal(missedEvent.run('violations'), 1, 'Polling recovers missed fullscreenchange once');
  await missedEvent.run('returnToFullscreen()');
  missedEvent.run('isPaused = true');
  missedEvent.context.document.fullscreenElement = null;
  missedEvent.dispatch('fullscreenchange');
  assert.equal(missedEvent.run('violations'), 1, 'Teacher pause records action without punishment');

  storage.removeItem('schooltest:attempt:42:v1');
  const telemetry = environment(async () => {throw Error('offline');});
  telemetry.run('setupBrowserEvents(); hasStarted = true; isOffline = true');
  let prevented = false;
  telemetry.dispatch('copy', {preventDefault() {prevented = true;}});
  telemetry.dispatch('keydown', {key: 'Escape'});
  assert.equal(prevented, true);
  assert.equal(telemetry.run('violations'), 0, 'Esc keydown alone is not a fullscreen exit');
  assert.equal(telemetry.run('pendingEvents.length'), 2);

  console.log('PASS: 17 student scenarios and 6 scripts parsed.');
  return {passed: true, scenarios: 17, scriptsParsed: 6};
}
module.exports = runTests();
