/**
 * monitor.js — WebSocket-клієнт для сторінки моніторингу сесії
 * Змінні з шаблону: SESSION_ID, IS_ACTIVE
 */

let ws = null;
let reconnectTimer = null;
let reconnectDelay = 2000;
let pingTimer = null;
let statusRequest = null;

// ---------------------------------------------------------------------------
// Ініціалізація
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  initializeMonitorAttempts();
  if (IS_ACTIVE) {
    connectWebSocket();
  } else {
    setWsStatus('disconnected', 'Сесія завершена');
  }

  // Автооновлення кількості учнів через API раз на 15 секунд
  if (IS_ACTIVE) {
    setInterval(fetchSessionStatus, 15000);
  }
});

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------
function connectWebSocket() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${protocol}//${location.host}/ws/monitor/${SESSION_ID}`;

  setWsStatus('connecting', 'Підключення...');

  ws = new WebSocket(url);

  ws.onopen = () => {
    setWsStatus('connected', 'Підключено');
    reconnectDelay = 2000;
    clearTimeout(reconnectTimer);
    startPing();
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data !== 'pong') handleWsEvent(data);
    } catch (e) { /* ігнорується */ }
  };

  ws.onclose = () => {
    clearInterval(pingTimer);
    setWsStatus('disconnected', 'З\'єднання перервано');
    scheduleReconnect();
  };

  ws.onerror = () => {
    setWsStatus('disconnected', 'Помилка з\'єднання');
  };
}

function scheduleReconnect() {
  if (!IS_ACTIVE) return;
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(() => {
    reconnectDelay = Math.min(reconnectDelay * 1.5, 30000);
    connectWebSocket();
  }, reconnectDelay);
}

function startPing() {
  clearInterval(pingTimer);
  pingTimer = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send('ping');
    }
  }, 25000);
}

// ---------------------------------------------------------------------------
// Обробка WS-подій
// ---------------------------------------------------------------------------
function handleWsEvent(data) {
  const event = data.event;
  const studentName = data.student_name || '';
  const attemptId   = data.attempt_id;

  addEventFeedItem(event, studentName, data);

  switch (event) {
    case 'answer_saved':
      // Оновити загальний стан з API (щоб отримати актуальні бали та перемалювати блок прогресу, якщо потрібно)
      fetchSessionStatus();
      
      const box = document.getElementById(`box-${attemptId}-${data.question_id}`);
      if (box) {
        box.className = 'progress-box'; // reset
        if (data.is_correct === true) {
          box.classList.add('progress-box--correct');
        } else if (data.is_correct === false) {
          box.classList.add('progress-box--incorrect');
        }
      }
      break;

    case 'test_finished':
    case 'timeout_auto_submit':
      updateStudentRow(attemptId, {
        status: data.status || (event === 'timeout_auto_submit' ? 'timeout' : 'finished'),
        score:  data.score,
        max_score: data.max_score,
        finished_at: data.finished_at || null,
      });
      incrementFinishedCount();
      fetchSessionStatus();
      break;

    case 'pause':
      updateStudentRow(attemptId, { status: 'paused' });
      break;

    case 'resume':
      updateStudentRow(attemptId, { status: data.status || 'in_progress' });
      break;

    case 'stop':
      updateStudentRow(attemptId, {
        status: 'stopped',
        score: data.score,
        max_score: data.max_score,
        finished_at: data.finished_at || null,
      });
      incrementFinishedCount();
      fetchSessionStatus();
      break;

    case 'tab_blur':
      updateStudentRow(attemptId, {violation_count: data.violation_count});
      break;

    case 'connection_lost':
      markConnectionLost(attemptId);
      break;

    case 'attempt_deleted':
      removeStudentRow(attemptId);
      decrementStudentCount();
      fetchSessionStatus();
      break;

    case 'reconnect':
      fetchSessionStatus();
      break;

    case 'login':
    case 'start_test':
    case 'mass_pause':
    case 'mass_resume':
    case 'mass_stop':
      fetchSessionStatus();
      break;
  }
}

// ---------------------------------------------------------------------------
// Оновлення рядка учня
// ---------------------------------------------------------------------------
const monitorAttempts = new Map();
const expandedMonitorGroups = new Set();
const completedMonitorStatuses = new Set(['finished', 'timeout', 'stopped']);
let monitorSort = 'status';
let monitorGrouping = 'student';

function monitorTime(value) {
  const time = Date.parse(value || '');
  return Number.isFinite(time) ? time : 0;
}

function monitorProgress(attempt) {
  const questions = attempt.assigned_questions || [];
  return questions.length ? questions.filter(q => q.is_correct !== null).length / questions.length : 0;
}

function compareMonitorAttempts(a, b) {
  let order = 0;
  if (monitorSort === 'newest') order = monitorTime(b.started_at) - monitorTime(a.started_at);
  else if (monitorSort === 'oldest') order = monitorTime(a.started_at) - monitorTime(b.started_at);
  else if (monitorSort === 'progress') order = monitorProgress(b) - monitorProgress(a);
  else if (monitorSort === 'status') {
    const ranks = {in_progress: 0, paused: 1, not_started: 2};
    order = (ranks[a.status] ?? 3) - (ranks[b.status] ?? 3);
  }
  return order || (a.student_name || '').localeCompare(b.student_name || '', 'uk', {numeric: true}) || a.id - b.id;
}

function displayMonitorTime(value) {
  if (!value) return '—';
  return monitorTime(value) ? new Date(value).toLocaleTimeString('uk-UA', {timeZone: 'Europe/Kyiv'}) : value;
}

function monitorScoreHtml(attempt) {
  if (attempt.score === null || attempt.score === undefined) return '<span class="text-muted">Ще не відповідав</span>';
  const maxGrade = typeof MAX_GRADE === 'undefined' ? 12 : MAX_GRADE;
  const grade = attempt.max_score > 0 ? Math.round(attempt.score / attempt.max_score * maxGrade) : '—';
  return `<div class="grade-val">${grade} <span class="text-muted">/ ${maxGrade}</span></div><div class="score-val text-muted">Бали: ${Number(attempt.score).toFixed(1)} / ${Number(attempt.max_score || 0).toFixed(1)}</div>`;
}

function updateStudentRow(attemptId, data, arrange = true) {
  const anchor = arrange ? captureMonitorAnchor() : null;
  attemptId = Number(attemptId);
  const previous = monitorAttempts.get(attemptId);
  if (!previous && !data.student_name) { fetchSessionStatus(); return; }
  const attempt = {...previous, ...data, id: attemptId};
  // A late HTTP snapshot must not undo an already received completion event.
  if (previous && completedMonitorStatuses.has(previous.status) && !completedMonitorStatuses.has(attempt.status)) return;
  monitorAttempts.set(attemptId, attempt);
  const tbody = document.getElementById('students-tbody');
  const cards = document.getElementById('monitor-mobile-cards');
  if (!tbody || !cards) return;
  document.getElementById('no-students-row')?.remove();
  cards.querySelectorAll(':scope > p').forEach(p => p.remove());
  let row = document.getElementById(`row-${attemptId}`);
  if (!row) {
    row = document.createElement('tr');
    row.id = `row-${attemptId}`;
    row.innerHTML = `<td><strong id="name-${attemptId}"></strong><span id="warning-${attemptId}"></span><span class="monitor-attempt-meta" id="attempt-meta-${attemptId}"></span></td><td><span id="status-${attemptId}"></span></td><td><div class="progress-container"></div></td><td id="score-${attemptId}"></td><td id="started-${attemptId}"></td><td id="finished-${attemptId}"></td><td id="actions-${attemptId}"></td>`;
    tbody.appendChild(row);
  }
  row.classList.add('monitor-attempt-row');
  let card = document.getElementById(`mcard-${attemptId}`);
  if (!card) {
    card = document.createElement('div');
    card.id = `mcard-${attemptId}`;
    card.className = 'monitor-student-card';
    card.innerHTML = `<div class="mcard-header"><strong class="mcard-name" id="mname-${attemptId}"></strong><span id="mwarning-${attemptId}"></span><span id="mstatus-${attemptId}"></span></div><div class="monitor-attempt-meta" id="mattempt-meta-${attemptId}"></div><div class="mcard-score" id="mscore-${attemptId}"></div><div class="mcard-actions" id="mactions-${attemptId}"></div>`;
    cards.appendChild(card);
  }
  for (const prefix of ['name', 'mname']) {
    const element = document.getElementById(`${prefix}-${attemptId}`);
    if (element) element.textContent = attempt.student_name;
  }
  const meta = [];
  if (attempt.session_attempt_count > 1) {
    const repeated = !completedMonitorStatuses.has(attempt.status) && attempt.session_attempt_number > 1;
    meta.push(`<span class="badge monitor-attempt-number">${repeated ? 'Повторно · ' : ''}Спроба #${Number(attempt.session_attempt_number)} з ${Number(attempt.session_attempt_count)}</span>`);
  }
  if (attempt.allow_device_transfer) meta.push('<span class="badge">🔓 Дозволено інший ПК</span>');
  for (const prefix of ['attempt-meta', 'mattempt-meta']) document.getElementById(`${prefix}-${attemptId}`).innerHTML = meta.join(' ');
  for (const prefix of ['warning', 'mwarning']) {
    const badge = document.getElementById(`${prefix}-${attemptId}`);
    badge.hidden = !attempt.violation_count;
    badge.textContent = ` ⚠️ ${attempt.violation_count || 0}`;
    badge.title = 'Виходи з повноекранного режиму або перемикання вікон';
  }
  for (const prefix of ['status', 'mstatus']) {
    const element = document.getElementById(`${prefix}-${attemptId}`);
    element.className = `badge badge-status badge-status--${attempt.status}`;
    element.textContent = statusLabel(attempt.status);
  }
  for (const prefix of ['actions', 'mactions']) {
    const element = document.getElementById(`${prefix}-${attemptId}`);
    const html = renderActionsHtml(attemptId, attempt.status, attempt.allow_device_transfer);
    if (element.innerHTML !== html) element.innerHTML = html;
  }
  for (const prefix of ['score', 'mscore']) document.getElementById(`${prefix}-${attemptId}`).innerHTML = monitorScoreHtml(attempt);
  document.getElementById(`started-${attemptId}`).textContent = displayMonitorTime(attempt.started_at);
  document.getElementById(`finished-${attemptId}`).textContent = displayMonitorTime(attempt.finished_at);
  row.querySelector('.progress-container').innerHTML = (attempt.assigned_questions || []).map((q, index) => {
    const state = q.is_correct === true ? 'progress-box--correct' : q.is_correct === false ? 'progress-box--incorrect' : '';
    return `<div class="progress-box ${state}" id="box-${attemptId}-${Number(q.question_id)}" title="Питання ${index + 1}"></div>`;
  }).join('');
  if (arrange) arrangeMonitorAttempts(anchor);
}

function appendMonitorAttempt(attempt, hidden, layout) {
  for (const [prefix, parent] of [['row', 'students-tbody'], ['mcard', 'monitor-mobile-cards']]) {
    const element = document.getElementById(`${prefix}-${attempt.id}`);
    if (!element) continue;
    element.hidden = hidden;
    element.dataset.status = attempt.status;
    layout.get(parent).push(element);
  }
}

function monitorHistorySummary(key, attempts, mobile) {
  const id = `${mobile ? 'mhistory' : 'history'}-${key}`;
  let summary = document.getElementById(id);
  if (!summary) {
    summary = document.createElement(mobile ? 'div' : 'tr');
    summary.id = id;
    summary.className = 'monitor-history-summary' + (mobile ? ' monitor-student-card' : '');
    summary.dataset.groupKey = key;
    const heading = '<div class="monitor-history-heading"><div><strong class="monitor-history-name"></strong><span class="monitor-history-meta text-muted"></span></div><button type="button" class="btn btn-sm btn-secondary" data-monitor-group="'+key+'"></button></div>';
    summary.innerHTML = mobile ? heading : '<td colspan="7">'+heading+'</td>';
  }
  const maxGrade = typeof MAX_GRADE === 'undefined' ? 12 : MAX_GRADE;
  const completed = attempts.filter(a => completedMonitorStatuses.has(a.status));
  const current = attempts.filter(a => !completedMonitorStatuses.has(a.status)).sort(compareMonitorAttempts);
  summary.querySelector('.monitor-history-name').textContent = (current[0] || attempts.at(-1)).student_name;
  const graded = completed.filter(a => a.max_score > 0 && a.score !== null && a.score !== undefined);
  const grade = a => Math.round(a.score / a.max_score * maxGrade);
  const best = graded.length ? Math.max(...graded.map(grade)) : '—';
  const latest = completed.at(-1);
  const previousGrade = latest && latest.max_score > 0 && latest.score !== null && latest.score !== undefined ? grade(latest) : '—';
  const parts = [];
  if (current.length) parts.push(`Зараз: Спроба #${current[0].session_attempt_number} · ${statusLabel(current[0].status)}`);
  if (current.length > 1) parts.push(`Поточних спроб: ${current.length}`);
  parts.push(`Завершених спроб: ${completed.length}`);
  if (completed.length) parts.push(`Попередня оцінка: ${previousGrade} / ${maxGrade}`, `Найкраща оцінка: ${best} / ${maxGrade}`);
  summary.querySelector('.monitor-history-meta').textContent = parts.join(' · ');
  summary.dataset.status = (current[0] || attempts.at(-1)).status;
  summary.classList.toggle('monitor-history-summary--active', current.length > 0);
  const button = summary.querySelector('button');
  const expanded = expandedMonitorGroups.has(key);
  button.hidden = !completed.length;
  button.textContent = `${expanded ? 'Згорнути' : current.length ? 'Історія' : 'Спроби'} (${completed.length}) ${expanded ? '▴' : '▾'}`;
  button.setAttribute('aria-expanded', String(expanded));
  button.setAttribute('aria-controls', completed.map(a => `${mobile ? 'mcard' : 'row'}-${a.id}`).join(' '));
  return summary;
}

function captureMonitorAnchor(preferred) {
  const candidates = preferred ? [preferred] : document.querySelectorAll('.monitor-history-summary, .monitor-attempt-row, #monitor-mobile-cards > .monitor-student-card');
  for (const element of candidates) {
    const rect = element.getBoundingClientRect();
    if (!element.hidden && rect.height > 0 && rect.top >= 0 && rect.top < window.innerHeight) return {element, top: rect.top};
  }
  return null;
}

function restoreMonitorAnchor(anchor) {
  if (!anchor?.element.isConnected || anchor.element.hidden) return;
  const delta = anchor.element.getBoundingClientRect().top - anchor.top;
  if (Math.abs(delta) > 1) window.scrollBy({top: delta, behavior: 'instant'});
}

function syncMonitorChildren(parent, elements) {
  // Re-appending every row temporarily collapses the table and resets scroll.
  // Keep existing rows in place; move only a row whose position really changed.
  let cursor = parent.firstElementChild;
  for (const element of elements) {
    if (element === cursor) cursor = cursor.nextElementSibling;
    else parent.insertBefore(element, cursor);
  }
}

function arrangeMonitorAttempts(anchor = captureMonitorAnchor()) {
  const tbody = document.getElementById('students-tbody');
  const cards = document.getElementById('monitor-mobile-cards');
  if (!tbody || !cards) return;
  const layout = new Map([['students-tbody', []], ['monitor-mobile-cards', []]]);
  const active = [], completed = new Map();
  for (const attempt of monitorAttempts.values()) {
    if (monitorGrouping === 'all' || (monitorGrouping === 'history' && !completedMonitorStatuses.has(attempt.status))) active.push(attempt);
    else {
      const key = attempt.student_key || `attempt-${attempt.id}`;
      if (!completed.has(key)) completed.set(key, []);
      completed.get(key).push(attempt);
    }
  }
  const groupedKeys = new Set([...completed].filter(([, rows]) => rows.length > 1).map(([key]) => key));
  document.querySelectorAll('.monitor-history-summary').forEach(summary => {
    if (!groupedKeys.has(summary.dataset.groupKey)) summary.remove();
  });
  const compare = (a, b) => Number(completedMonitorStatuses.has(a.status)) - Number(completedMonitorStatuses.has(b.status)) || compareMonitorAttempts(a, b);
  active.sort(compare).forEach(a => appendMonitorAttempt(a, false, layout));
  const representative = rows => [...rows].sort(compare)[0];
  const histories = [...completed.entries()].sort((a, b) => compare(representative(a[1]), representative(b[1])));
  for (const [key, attempts] of histories) {
    attempts.sort((a,b) => (a.session_attempt_number || 1) - (b.session_attempt_number || 1) || a.id-b.id);
    if (attempts.length > 1) {
      layout.get('students-tbody').push(monitorHistorySummary(key, attempts, false));
      layout.get('monitor-mobile-cards').push(monitorHistorySummary(key, attempts, true));
    }
    for (const attempt of [...attempts].sort(compare)) appendMonitorAttempt(attempt,
      attempts.length > 1 && completedMonitorStatuses.has(attempt.status) && !expandedMonitorGroups.has(key), layout);
  }
  syncMonitorChildren(tbody, layout.get('students-tbody'));
  syncMonitorChildren(cards, layout.get('monitor-mobile-cards'));
  document.getElementById('student-count').textContent = monitorAttempts.size;
  document.getElementById('finished-count').textContent = [...monitorAttempts.values()].filter(a => completedMonitorStatuses.has(a.status)).length;
  if (!monitorAttempts.size) {
    tbody.innerHTML = '<tr id="no-students-row"><td colspan="7" class="text-center text-muted">Учні ще не підключились</td></tr>';
    cards.innerHTML = '<p class="text-muted">Учні ще не підключились</p>';
  }
  restoreMonitorAnchor(anchor);
}

function initializeMonitorAttempts() {
  if (typeof INITIAL_MONITOR_ATTEMPTS === 'undefined') return;
  const select = document.getElementById('monitor-sort');
  const validSorts = ['status','name','newest','oldest','progress'];
  try { const saved = localStorage.getItem('schooltest:monitor-sort'); if (validSorts.includes(saved)) monitorSort = saved; } catch {}
  select.value = monitorSort;
  const grouping = document.getElementById('monitor-grouping');
  const validGroups = ['student', 'history', 'all'];
  try { const saved = localStorage.getItem('schooltest:monitor-grouping'); if (validGroups.includes(saved)) monitorGrouping = saved; } catch {}
  grouping.value = monitorGrouping;
  grouping.addEventListener('change', () => {
    monitorGrouping = validGroups.includes(grouping.value) ? grouping.value : 'student';
    try { localStorage.setItem('schooltest:monitor-grouping', monitorGrouping); } catch {}
    arrangeMonitorAttempts();
  });
  for (const attempt of INITIAL_MONITOR_ATTEMPTS) updateStudentRow(attempt.id, attempt, false);
  arrangeMonitorAttempts();
  select.addEventListener('change', () => {
    monitorSort = validSorts.includes(select.value) ? select.value : 'status';
    try { localStorage.setItem('schooltest:monitor-sort', monitorSort); } catch {}
    arrangeMonitorAttempts();
  });
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-monitor-group]');
    if (!button) return;
    event.preventDefault();
    const anchor = captureMonitorAnchor(button);
    const key = button.dataset.monitorGroup;
    if (expandedMonitorGroups.has(key)) expandedMonitorGroups.delete(key); else expandedMonitorGroups.add(key);
    arrangeMonitorAttempts(anchor);
  });
}


function markConnectionLost(attemptId) {
  const statusEl = document.getElementById(`status-${attemptId}`);
  if (statusEl && statusEl.textContent === statusLabel('in_progress')) {
    statusEl.style.opacity = '0.5';
  }
}

function incrementFinishedCount() { arrangeMonitorAttempts(); }

// ---------------------------------------------------------------------------
// Feed подій
// ---------------------------------------------------------------------------
function addEventFeedItem(eventType, studentName, data) {
  const feed = document.getElementById('event-feed');
  const noMsg = document.getElementById('no-events-msg');
  if (noMsg) noMsg.remove();

  const now = data.finished_at ? displayMonitorTime(data.finished_at)
    : new Date().toLocaleTimeString('uk-UA', {timeZone: 'Europe/Kyiv'});

  const eventNames = {
    'login': 'Вхід',
    'start_test': 'Початок тесту',
    'answer_saved': 'Збережено відповідь',
    'question_changed': 'Дія учня / зміна питання',
    'test_finished': 'Завершення тесту',
    'connection_lost': 'Втрачено з\'єднання',
    'reconnect': 'Відновлено з\'єднання',
    'tab_blur': 'Порушення режиму тестування',
    'tab_focus': 'Повернення до тесту',
    'timeout_auto_submit': 'Автоматичне завершення',
    'pause_test': 'Тест на паузі',
    'resume_test': 'Тест відновлено',
    'stop': 'Тест зупинено',
    'pause': 'Тест на паузі',
    'resume': 'Тест відновлено',
    'attempt_deleted': 'Спробу анульовано',
    'question_skipped': 'Пропустив питання',
    'question_returned': 'Повернувся до пропущеного питання'
  };
  const eventName = eventNames[eventType] || eventType;

  let detail = '';
  if (data.details) {
    detail = data.details;
  } else if (eventType === 'answer_saved' || eventType === 'question_skipped' || eventType === 'question_returned') {
    const box = document.getElementById(`box-${data.attempt_id}-${data.question_id}`);
    const qNum = box ? box.getAttribute('title') : null;
    detail = qNum ? qNum.toLowerCase() : `питання (ID: ${data.question_id})`;
  } else if (eventType === 'test_finished') {
    detail = `${data.score || 0} / ${data.max_score || 0} б.`;
  } else if (eventType === 'connection_lost') {
    detail = 'з\'єднання перервано';
  }

  // Перевіряємо, чи можна згрупувати з останньою подією
  const firstItem = feed.firstElementChild;
  if (firstItem && 
      firstItem.dataset.eventType === eventType && 
      firstItem.dataset.studentName === (studentName || '') && 
      firstItem.dataset.detail === (detail || '')) {
      
      let count = parseInt(firstItem.dataset.count || '1') + 1;
      firstItem.dataset.count = count;
      
      // Оновлюємо час на останній
      const timeSpan = firstItem.querySelector('.event-time');
      if (timeSpan) timeSpan.textContent = now;
      
      // Оновлюємо бейдж лічильника
      let countSpan = firstItem.querySelector('.event-count');
      if (countSpan) {
          countSpan.textContent = `x${count}`;
          countSpan.style.display = 'inline-block';
      }
      
      // Блимаємо елементом для привернення уваги (опціонально)
      firstItem.style.transition = 'background-color 0.2s';
      firstItem.style.backgroundColor = 'var(--bg-color)';
      setTimeout(() => { firstItem.style.backgroundColor = ''; }, 200);
      
      return; // Не додаємо новий елемент, подія згрупована
  }

  const item = document.createElement('div');
  item.className = 'event-item';
  item.dataset.eventType = eventType;
  item.dataset.studentName = studentName || '';
  item.dataset.detail = detail || '';
  item.dataset.count = 1;
  item.innerHTML = `
    <span class="event-time">${now}</span>
    <span class="event-name">${escHtml(studentName)}</span>
    <span class="event-badge event-badge--${eventType}">${escHtml(eventName)} <span class="event-count" style="display:none; background: rgba(0,0,0,0.2); border-radius: 10px; padding: 1px 5px; margin-left: 4px; font-size: 0.8em;"></span></span>
    ${detail ? `<span class="event-detail">${escHtml(detail)}</span>` : ''}
  `;

  feed.prepend(item);

  // Обмежуємо до 50 записів
  while (feed.children.length > 50) feed.removeChild(feed.lastChild);
}

// ---------------------------------------------------------------------------
// API fallback — отримати свіжий статус сесії
// ---------------------------------------------------------------------------
async function fetchSessionStatus() {
  // Answer bursts and the fallback poll can overlap; share one current request.
  if (statusRequest) return statusRequest;
  statusRequest = loadSessionStatus();
  try { await statusRequest; }
  finally { statusRequest = null; }
}

async function loadSessionStatus() {
  try {
    const resp = await fetch(`/api/session-status/${SESSION_ID}`);
    if (!resp.ok) return;
    const data = await resp.json();
    const anchor = captureMonitorAnchor();
    const ids = new Set(data.attempts.map(a => Number(a.id)));
    for (const id of monitorAttempts.keys()) if (!ids.has(id)) removeStudentRow(id, false);
    data.attempts.forEach(a => updateStudentRow(a.id, a, false));
    arrangeMonitorAttempts(anchor);
  } catch { /* ігнорується */ }
}

// ---------------------------------------------------------------------------
// WS status UI
// ---------------------------------------------------------------------------
function setWsStatus(state, label) {
  const dot   = document.getElementById('ws-dot');
  const lbl   = document.getElementById('ws-label');
  if (!dot || !lbl) return;
  dot.className = `ws-dot ${state}`;
  lbl.textContent = label;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function refreshPage() { location.reload(); }

function statusLabel(status) {
  const map = {
    not_started: 'Не розпочав',
    in_progress: 'Проходить',
    paused:      'Пауза',
    stopped:     'Зупинено',
    finished:    'Завершив',
    timeout:     'Час вийшов',
  };
  return map[status] || status;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ---------------------------------------------------------------------------
// Actions rendering and API requests
// ---------------------------------------------------------------------------
function renderActionsHtml(attemptId, status, allowDeviceTransfer) {
  if (['finished', 'timeout', 'stopped'].includes(status)) {
    return `
      <div class="action-group">
        <a href="/teacher/results/${attemptId}" class="btn btn-sm btn-secondary">Деталі</a>
        <button type="button" data-action="delete-attempt" data-attempt-id="${attemptId}" class="btn btn-sm btn-outline-danger" title="Видалити спробу" style="padding: 0.2rem 0.5rem; font-size: 0.8rem;">🗑️</button>
      </div>
    `;
  } else if (status === 'in_progress') {
    return `
      <div class="action-group">
        <button type="button" data-action="pause" data-attempt-id="${attemptId}" class="btn btn-sm btn-warning" title="Поставити на паузу">⏸ Пауза</button>
        <button type="button" data-action="allow-transfer" data-attempt-id="${attemptId}" class="btn btn-sm btn-info" title="Дозволити вхід з іншого ПК">🔓 Інший ПК</button>
        <button type="button" data-action="stop" data-attempt-id="${attemptId}" class="btn btn-sm btn-danger" title="Зупинити">⏹ Стоп</button>
      </div>
    `;
  } else if (status === 'paused') {
    return `
      <div class="action-group">
        <button type="button" data-action="resume" data-attempt-id="${attemptId}" class="btn btn-sm btn-success" title="Відновити">▶ Відновити</button>
        <button type="button" data-action="allow-transfer" data-attempt-id="${attemptId}" class="btn btn-sm btn-info" title="Дозволити вхід з іншого ПК">🔓 Інший ПК</button>
        <button type="button" data-action="stop" data-attempt-id="${attemptId}" class="btn btn-sm btn-danger" title="Зупинити">⏹ Стоп</button>
      </div>
    `;
  } else if (status === 'not_started') {
    return `
      <div class="action-group">
        <button type="button" data-action="allow-transfer" data-attempt-id="${attemptId}" class="btn btn-sm btn-info" title="Дозволити вхід з іншого ПК">🔓 Інший ПК</button>
        <button type="button" data-action="delete-attempt" data-attempt-id="${attemptId}" class="btn btn-sm btn-danger" title="Анулювати реєстрацію">🗑️ Анулювати</button>
      </div>
    `;
  }
  return '';
}

async function pauseAttempt(attemptId) {
  try {
    const res = await fetch(`/teacher/attempts/${attemptId}/pause`, { method: 'POST' });
    if (res.ok) {
      updateStudentRow(attemptId, { status: 'paused' });
    } else {
      const err = await res.json().catch(() => ({}));
      alert('Помилка: ' + (err.detail || `HTTP ${res.status}`));
    }
  } catch (e) {
    alert('Помилка мережі: ' + e.message);
  }
}

async function resumeAttempt(attemptId) {
  try {
    const res = await fetch(`/teacher/attempts/${attemptId}/resume`, { method: 'POST' });
    if (res.ok) {
      await fetchSessionStatus();
    } else {
      const err = await res.json().catch(() => ({}));
      alert('Помилка: ' + (err.detail || `HTTP ${res.status}`));
    }
  } catch (e) {
    alert('Помилка мережі: ' + e.message);
  }
}

async function stopAttempt(attemptId) {
  if (!confirm('Ви дійсно бажаєте зупинити тест для цього учня?')) return;
  try {
    const res = await fetch(`/teacher/attempts/${attemptId}/stop`, { method: 'POST' });
    if (res.ok) {
      const data = await res.json();
      updateStudentRow(attemptId, {
        status: 'stopped',
        score: data.score,
        max_score: data.max_score,
        finished_at: data.finished_at || null,
      });
      incrementFinishedCount();
      fetchSessionStatus();
    } else {
      const err = await res.json().catch(() => ({}));
      alert('Помилка: ' + (err.detail || `HTTP ${res.status}`));
    }
  } catch (e) {
    alert('Помилка мережі: ' + e.message);
  }
}

async function allowTransferAttempt(attemptId) {
  const nameEl = document.getElementById(`name-${attemptId}`);
  const sName = nameEl ? nameEl.textContent.trim() : 'учня';
  if (!confirm(`Дозволити для ${sName} вхід з іншого комп'ютера або браузера?\nЯкщо тест триває, його буде безпечно призупинено для переходу.`)) return;
  try {
    const res = await fetch(`/teacher/attempts/${attemptId}/allow-device-transfer`, { method: 'POST' });
    if (res.ok) {
      const data = await res.json();
      alert(data.message || 'Вхід з іншого пристрою дозволено! Тепер учень може увійти під своїм ім\'ям з іншого ПК.');
      fetchSessionStatus();
    } else {
      const err = await res.json().catch(() => ({}));
      alert('Помилка: ' + (err.detail || `HTTP ${res.status}`));
    }
  } catch (e) {
    alert('Помилка мережі: ' + e.message);
  }
}

async function deleteAttempt(attemptId) {
  const nameEl = document.getElementById(`name-${attemptId}`);
  const sName = nameEl ? nameEl.textContent.trim() : 'учня';
  if (!confirm(`Ви дійсно бажаєте видалити спробу / реєстрацію для ${sName}?\nЦю дію неможливо скасувати.`)) return;
  try {
    const res = await fetch(`/teacher/attempts/${attemptId}/delete`, { method: 'POST' });
    if (res.ok) {
      removeStudentRow(attemptId);
      decrementStudentCount();
      fetchSessionStatus();
    } else {
      const err = await res.json().catch(() => ({}));
      alert('Помилка: ' + (err.detail || `HTTP ${res.status}`));
    }
  } catch (e) {
    alert('Помилка мережі: ' + e.message);
  }
}

function removeStudentRow(attemptId, arrange = true) {
  attemptId = Number(attemptId);
  monitorAttempts.delete(attemptId);
  document.getElementById(`row-${attemptId}`)?.remove();
  document.getElementById(`mcard-${attemptId}`)?.remove();
  if (arrange) arrangeMonitorAttempts();
}

function decrementStudentCount() { arrangeMonitorAttempts(); }
