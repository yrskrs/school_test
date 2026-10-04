/* One durable cache per attempt; no correct answers or server credentials. */
(function (global) {
  function emptyState() {
    return {version: 1, answers: {}, pending: {}, events: [], skipped: [],
      violations: 0, timeLeft: null, questionTimes: {}, finish: null};
  }
  function create(attemptId, storage) {
    const key = `schooltest:attempt:${attemptId}:v1`;
    let state = emptyState();
    let available = true;
    try {
      const saved = JSON.parse(storage.getItem(key) || 'null');
      const object = value => value && typeof value === 'object' && !Array.isArray(value);
      if (saved && saved.version === 1 && object(saved.answers) && object(saved.pending)
          && object(saved.questionTimes) && Array.isArray(saved.events) && Array.isArray(saved.skipped)) {
        state.answers = saved.answers;
        state.pending = Object.fromEntries(Object.entries(saved.pending).filter(([id, body]) =>
          object(body) && Number.isInteger(body.question_id) && body.question_id > 0 && Number(id) === body.question_id));
        state.events = saved.events.filter(event => object(event) && typeof event.event_type === 'string');
        state.skipped = saved.skipped.filter(id => Number.isInteger(id) && id > 0);
        state.violations = Number.isInteger(saved.violations) && saved.violations >= 0 ? saved.violations : 0;
        state.timeLeft = Number.isFinite(saved.timeLeft) && saved.timeLeft >= 0 ? saved.timeLeft : null;
        state.questionTimes = Object.fromEntries(Object.entries(saved.questionTimes).filter(([, time]) => Number.isFinite(time) && time >= 0));
        state.finish = object(saved.finish) && typeof saved.finish.timeout === 'boolean' ? saved.finish : null;
      }
    } catch { available = false; }
    return {
      get state() { return state; },
      get available() { return available; },
      save(patch) {
        state = {...state, ...patch};
        try { storage.setItem(key, JSON.stringify(state)); available = true; return true; }
        catch { available = false; return false; }
      },
      clear() {
        try { storage.removeItem(key); } catch { /* Only this attempt's key. */ }
        state = emptyState();
      },
      download() {
        const blob = new Blob([JSON.stringify({attempt_id: attemptId, ...state}, null, 2)], {type: 'application/json'});
        const url = URL.createObjectURL(blob);
        const link = global.document.createElement('a');
        link.href = url;
        link.download = `schooltest-attempt-${attemptId}-local.json`;
        global.document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      },
    };
  }
  global.StudentCache = {create};
}(typeof window === 'undefined' ? globalThis : window));
