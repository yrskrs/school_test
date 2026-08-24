/**
 * monitor.js — WebSocket-клієнт для сторінки моніторингу сесії
 * Змінні з шаблону: SESSION_ID, IS_ACTIVE
 */

let ws = null;
let reconnectTimer = null;
let reconnectDelay = 2000;

// ---------------------------------------------------------------------------
// Ініціалізація
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
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
  setInterval(() => {
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
        finished_at: new Date().toLocaleTimeString('uk-UA'),
      });
      incrementFinishedCount();
      break;

    case 'pause':
      updateStudentRow(attemptId, { status: 'paused' });
      break;

    case 'resume':
      updateStudentRow(attemptId, { status: 'in_progress' });
      break;

    case 'stop':
      updateStudentRow(attemptId, {
        status: 'stopped',
        score: data.score,
        max_score: data.max_score,
        finished_at: new Date().toLocaleTimeString('uk-UA'),
      });
      incrementFinishedCount();
      break;

    case 'tab_blur':
      let nameEl = document.getElementById(`name-${attemptId}`);
      if (nameEl) {
        let warningBadge = document.getElementById(`warning-${attemptId}`);
        if (!warningBadge) {
          warningBadge = document.createElement('span');
          warningBadge.id = `warning-${attemptId}`;
          warningBadge.title = "Учень перемикав вкладки або згортав браузер";
          warningBadge.style.cursor = "help";
          warningBadge.innerHTML = ' ⚠️ <span style="font-size:0.75rem; font-weight:bold; color:var(--warning)" class="blur-count">1</span>';
          nameEl.parentNode.appendChild(warningBadge);
        } else {
          let countSpan = warningBadge.querySelector('.blur-count');
          countSpan.textContent = parseInt(countSpan.textContent) + 1;
        }
      }
      break;

    case 'connection_lost':
      markConnectionLost(attemptId);
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
function updateStudentRow(attemptId, data) {
  let row = document.getElementById(`row-${attemptId}`);

  if (!row) {
    // Новий учень — додаємо рядок
    const tbody = document.getElementById('students-tbody');
    const noRow = document.getElementById('no-students-row');
    if (noRow) noRow.remove();

    row = document.createElement('tr');
    row.id = `row-${attemptId}`;
    let progressHtml = '<div class="progress-container">';
    if (data.assigned_questions && data.assigned_questions.length > 0) {
      data.assigned_questions.forEach((q, index) => {
        let pclass = '';
        if (q.is_correct === true) {
          pclass = 'progress-box--correct';
        } else if (q.is_correct === false) {
          pclass = 'progress-box--incorrect';
        }
        progressHtml += `<div class="progress-box ${pclass}" id="box-${attemptId}-${q.question_id}" title="Питання ${index + 1}"></div>`;
      });
    }
    progressHtml += '</div>';

    row.innerHTML = `
      <td><strong id="name-${attemptId}"></strong></td>
      <td><span class="badge badge-status" id="status-${attemptId}"></span></td>
      <td>${progressHtml}</td>
      <td id="score-${attemptId}">—</td>
      <td id="started-${attemptId}">—</td>
      <td id="finished-${attemptId}">—</td>
      <td id="actions-${attemptId}"></td>
    `;
    tbody.appendChild(row);

    const count = parseInt(document.getElementById('student-count').textContent) || 0;
    document.getElementById('student-count').textContent = count + 1;
  } else {
    // Якщо рядок існує, оновлюємо плашки
    const progressContainer = row.querySelector('.progress-container');
    if (progressContainer && data.assigned_questions) {
      let progressHtml = '';
      data.assigned_questions.forEach((q, index) => {
        let pclass = '';
        if (q.is_correct === true) {
          pclass = 'progress-box--correct';
        } else if (q.is_correct === false) {
          pclass = 'progress-box--incorrect';
        }
        progressHtml += `<div class="progress-box ${pclass}" id="box-${attemptId}-${q.question_id}" title="Питання ${index + 1}"></div>`;
      });
      progressContainer.innerHTML = progressHtml;
    }
  }

  const nameEl = document.getElementById(`name-${attemptId}`);
  if (nameEl && data.student_name) nameEl.textContent = data.student_name;

  if (data.status) {
    const statusEl = document.getElementById(`status-${attemptId}`);
    if (statusEl) {
      statusEl.className = `badge badge-status badge-status--${data.status}`;
      statusEl.textContent = statusLabel(data.status);
    }
    const actEl = document.getElementById(`actions-${attemptId}`);
    if (actEl) {
      actEl.innerHTML = renderActionsHtml(attemptId, data.status);
    }
  }

  if (data.score !== undefined && data.score !== null) {
    const scoreEl = document.getElementById(`score-${attemptId}`);
    if (scoreEl) {
      let grade = "—";
      if (data.max_score > 0) {
        grade = Math.round((data.score / data.max_score) * (typeof MAX_GRADE !== 'undefined' ? MAX_GRADE : 12));
      }
      const maxGradeVal = typeof MAX_GRADE !== 'undefined' ? MAX_GRADE : 12;
      const gradeHtml = `<div class="grade-val" style="font-size: 1.15rem; font-weight: 700; color: var(--info);">${grade} <span style="font-size: 0.85rem; font-weight: normal; color: var(--text-muted);">/ ${maxGradeVal}</span></div>`;
      const scoreSubHtml = `<div class="score-val" style="font-size: 0.85rem; color: var(--text-muted); margin-top: 2px;">Бали: ${Number(data.score).toFixed(1)} / ${Number(data.max_score).toFixed(1)}</div>`;
      scoreEl.innerHTML = gradeHtml + scoreSubHtml;
    }
  }

  if (data.finished_at) {
    const finEl = document.getElementById(`finished-${attemptId}`);
    if (finEl) finEl.textContent = data.finished_at;
  }
}

function markConnectionLost(attemptId) {
  const statusEl = document.getElementById(`status-${attemptId}`);
  if (statusEl && statusEl.textContent === statusLabel('in_progress')) {
    statusEl.style.opacity = '0.5';
  }
}

function incrementFinishedCount() {
  const el = document.getElementById('finished-count');
  if (el) el.textContent = (parseInt(el.textContent) || 0) + 1;
}

// ---------------------------------------------------------------------------
// Feed подій
// ---------------------------------------------------------------------------
function addEventFeedItem(eventType, studentName, data) {
  const feed = document.getElementById('event-feed');
  const noMsg = document.getElementById('no-events-msg');
  if (noMsg) noMsg.remove();

  const now = new Date().toLocaleTimeString('uk-UA');

  const eventNames = {
    'login': 'Вхід',
    'start_test': 'Початок тесту',
    'answer_saved': 'Збережено відповідь',
    'question_changed': 'Зміна питання',
    'test_finished': 'Завершення тесту',
    'connection_lost': 'Втрачено з\'єднання',
    'reconnect': 'Відновлено з\'єднання',
    'tab_blur': 'Згорнув вкладку',
    'tab_focus': 'Розгорнув вкладку',
    'timeout_auto_submit': 'Автоматичне завершення',
    'pause_test': 'Тест на паузі',
    'resume_test': 'Тест відновлено',
    'stop_test': 'Тест зупинено',
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
  try {
    const resp = await fetch(`/api/session-status/${SESSION_ID}`);
    if (!resp.ok) return;
    const data = await resp.json();

    data.attempts.forEach(a => {
      updateStudentRow(a.id, {
        student_name: a.student_name,
        status: a.status,
        score: a.score,
        max_score: a.max_score,
        finished_at: a.finished_at ? new Date(a.finished_at).toLocaleTimeString('uk-UA') : null,
        assigned_questions: a.assigned_questions,
      });
    });

    document.getElementById('student-count').textContent = data.attempts.length;
    const finished = data.attempts.filter(a => ['finished', 'timeout'].includes(a.status)).length;
    document.getElementById('finished-count').textContent = finished;
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
function renderActionsHtml(attemptId, status) {
  if (['finished', 'timeout', 'stopped'].includes(status)) {
    return `<a href="/teacher/results/${attemptId}" class="btn btn-sm btn-secondary">Деталі</a>`;
  } else if (status === 'in_progress') {
    return `
      <div class="action-group">
        <button type="button" data-action="pause" data-attempt-id="${attemptId}" class="btn btn-sm btn-warning">⏸ Пауза</button>
        <button type="button" data-action="stop" data-attempt-id="${attemptId}" class="btn btn-sm btn-danger">⏹ Зупинити</button>
      </div>
    `;
  } else if (status === 'paused') {
    return `
      <div class="action-group">
        <button type="button" data-action="resume" data-attempt-id="${attemptId}" class="btn btn-sm btn-success">▶ Відновити</button>
        <button type="button" data-action="stop" data-attempt-id="${attemptId}" class="btn btn-sm btn-danger">⏹ Зупинити</button>
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
      updateStudentRow(attemptId, { status: 'in_progress' });
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
        finished_at: new Date().toLocaleTimeString('uk-UA'),
      });
      incrementFinishedCount();
    } else {
      const err = await res.json().catch(() => ({}));
      alert('Помилка: ' + (err.detail || `HTTP ${res.status}`));
    }
  } catch (e) {
    alert('Помилка мережі: ' + e.message);
  }
}
