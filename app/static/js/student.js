/**
 * student.js — Логіка проходження тесту
 * Змінні з шаблону: ATTEMPT_ID, SESSION_ID, TEST_DATA, SAVED_ANSWERS, TIME_REMAINING
 */

// ---------------------------------------------------------------------------
// Стан
// ---------------------------------------------------------------------------
let currentIndex  = 0;          // index in questions[] of currently displayed question
let currentQueueIdx = 0;        // index in questionQueue[]
let timerInterval = null;
let timeLeft      = TIME_REMAINING;   // null якщо без обмеження
let qTimerInterval = null;
let qTimeLeft      = TEST_DATA.time_limit_per_question || null;
let isSaving      = false;
let isPaused      = ATTEMPT_STATUS === 'paused';
let isOffline     = false;
let lastFailedAnswer = null;
let answers       = {};               // { question_id: selectedOptions[] | textValue | dict }
let sequenceOrders = {};             // local sequence order for display
let matchingPools = {};              // local shuffled matching options for pool
let lockedQuestions = new Set();      // per-question timer expired
let skippedQuestions = new Set(window.SKIPPED_QUESTIONS || []);     // manually skipped by student
let isInitialLoad = true;
let timedOutQuestions = new Set();    // expired without answer

// Anti-Cheat
let violations = 0;
const MAX_VIOLATIONS = 3;
let isViolationShowing = false;

const questions = TEST_DATA.questions;
const totalQ    = questions.length;

// Dynamic queue of question indices to show
let questionQueue = [];

// Для matching та sequence потрібно перемішувати варіанти, щоб не показувати правильну відповідь
function shuffleArray(array) {
  const arr = [...array];
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

// ---------------------------------------------------------------------------
// Ініціалізація
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  // Завантажуємо збережені відповіді
  if (SAVED_ANSWERS) {
    Object.entries(SAVED_ANSWERS).forEach(([qid, val]) => {
      answers[parseInt(qid)] = val;
      if (val === "") {
        timedOutQuestions.add(parseInt(qid));
      }
    });
  }

  // Ініціалізація базових станів для sequence та matching
  questions.forEach(q => {
    if (q.question_type === 'sequence') {
      const saved = answers[q.id];
      if (Array.isArray(saved) && saved.length === q.options.length) {
        sequenceOrders[q.id] = saved.map(id => parseInt(id));
      } else {
        sequenceOrders[q.id] = shuffleArray(q.options).map(o => o.id);
      }
    } else if (q.question_type === 'matching') {
      const rightSides = q.options.map(o => o.matching_text).filter(Boolean);
      matchingPools[q.id] = shuffleArray(rightSides);
      if (answers[q.id] && typeof answers[q.id] === 'object' && !Array.isArray(answers[q.id])) {
        const formatted = {};
        Object.entries(answers[q.id]).forEach(([k, v]) => {
          if (v) formatted[parseInt(k)] = v;
        });
        answers[q.id] = formatted;
      } else {
        answers[q.id] = {};
      }
    }
  });

  // Locked questions for per-question time limits
  if (TEST_DATA.time_limit_per_question) {
    Object.keys(answers).forEach(qid => {
      lockedQuestions.add(parseInt(qid));
    });
  }

  // Build initial queue: all question indices (those not already locked/answered after reload)
  initQueue();

  buildNavigator();

  if (questionQueue.length === 0) {
    finishTest();
    return;
  }

  renderFromQueue(0);
  startTimer();
  setupBrowserEvents();

  if (isPaused) {
    handlePauseEvent();
  }
  connectStudentWebSocket();
  setInterval(fallbackCheckStatus, 5000);

  document.getElementById('total-count').textContent = totalQ;
});

function initQueue() {
  questionQueue = [];
  if (TEST_DATA.time_limit_per_question) {
    // Skip already-locked (answered in previous session)
    questions.forEach((q, i) => {
      if (!lockedQuestions.has(q.id)) questionQueue.push(i);
    });
  } else {
    questions.forEach((q, i) => questionQueue.push(i));
  }
}

// ---------------------------------------------------------------------------
// Навігатор питань
// ---------------------------------------------------------------------------
function buildNavigator() {
  const nav = document.getElementById('q-nav');
  nav.innerHTML = '';
  questions.forEach((q, i) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'q-nav-btn';
    btn.id = `nav-btn-${i}`;
    btn.textContent = i + 1;
    // No onclick — navigator is display-only
    nav.appendChild(btn);
  });
}

function updateNavigator() {
  questions.forEach((q, i) => {
    const btn = document.getElementById(`nav-btn-${i}`);
    if (!btn) return;
    btn.className = 'q-nav-btn';
    if (i === currentIndex) btn.classList.add('active');
    if (timedOutQuestions.has(q.id)) {
      btn.classList.add('timed-out');
    } else if (skippedQuestions.has(q.id)) {
      btn.classList.add('skipped');
    } else if (hasAnswer(q.id)) {
      btn.classList.add('answered');
    }
    if (lockedQuestions.has(q.id)) {
      btn.classList.add('locked');
      btn.disabled = true;
    } else {
      btn.disabled = false;
    }
  });
}

function hasAnswer(questionId) {
  const ans = answers[questionId];
  if (ans === undefined || ans === null) return false;
  if (Array.isArray(ans)) return ans.length > 0;
  if (typeof ans === 'object') return Object.keys(ans).length > 0;
  return String(ans).trim() !== '';
}

function validateAnswer() {
  const btnNext = document.getElementById('btn-next');
  if (!btnNext) return;
  const qId = questions[currentIndex]?.id;
  btnNext.disabled = !hasAnswer(qId);
}

// ---------------------------------------------------------------------------
// Рендер питання
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Рендер питання з черги
// ---------------------------------------------------------------------------
function renderFromQueue(queueIdx) {
  if (queueIdx < 0 || queueIdx >= questionQueue.length) {
    // Черга вичерпана — завершення тесту
    finishTest();
    return;
  }
  currentQueueIdx = queueIdx;
  renderQuestion(questionQueue[queueIdx]);
}

function renderQuestion(index) {
  if (index < 0 || index >= totalQ) return;

  if (TEST_DATA.time_limit_per_question && currentIndex !== index) {
    lockedQuestions.add(questions[currentIndex].id);
  }

  const prevQ = questions[currentIndex];
  const nextQ = questions[index];
  if (!isInitialLoad && prevQ && prevQ.id !== nextQ.id) {
    if (skippedQuestions.has(nextQ.id)) {
      logBrowserEvent('question_returned', `question_id=${nextQ.id}`);
    }
  }
  isInitialLoad = false;

  currentIndex = index;
  const q = questions[index];

  document.getElementById('current-q-num').textContent = currentQueueIdx + 1;
  document.getElementById('total-q-num').textContent   = questionQueue.length;
  document.getElementById('progress-bar').style.width = `${((currentQueueIdx + 1) / (questionQueue.length || 1)) * 100}%`;

  const card = document.getElementById('question-card');
  card.innerHTML = buildQuestionHTML(q, index);

  // Відновлюємо збережену відповідь
  restoreAnswer(q);

  // Кнопки навігації: замінюємо "Попереднє" на "Пропустити"
  // Показуємо skip якщо є ще питання після поточного
  const skipBtn = document.getElementById('btn-skip');
  const nextBtn = document.getElementById('btn-next');
  const textEl  = document.getElementById('last-question-text');

  const isLast = currentQueueIdx >= questionQueue.length - 1;

  if (skipBtn) skipBtn.style.display = isLast ? 'none' : 'inline-flex';
  if (nextBtn) nextBtn.style.display = 'inline-flex'; // завжди
  if (textEl)  textEl.style.display  = 'none';
  document.getElementById('btn-finish').style.display = 'inline-flex';

  updateNavigator();
  updateAnsweredCount();
  validateAnswer();
  resetQTimer();
}

function buildQuestionHTML(q, index) {
  let optionsHTML = '';
  const type = q.question_type;

  let hintText = '';
  switch (type) {
    case 'single_choice': hintText = 'Оберіть одну правильну відповідь.'; break;
    case 'true_false': hintText = 'Оберіть правильний варіант.'; break;
    case 'multiple_choice': hintText = 'Оберіть декілька правильних відповідей.'; break;
    case 'image_choice': hintText = 'Оберіть одне правильне зображення.'; break;
    case 'hotspot': hintText = 'Натисніть на правильне місце на зображенні.'; break;
    case 'short_text': hintText = 'Введіть вашу коротку відповідь у текстове поле.'; break;
    case 'matching': hintText = 'Встановіть відповідність, перетягнувши відповіді у відповідні зони.'; break;
    case 'sequence': hintText = 'Розставте елементи у правильній послідовності (за допомогою стрілок ▲▼).'; break;
  }

  if (type === 'single_choice' || type === 'true_false') {
    optionsHTML = `<div class="options-list-student">` +
      q.options.map(o => `
        <label class="option-item" id="opt-label-${o.id}">
          <input type="radio" name="q${q.id}" value="${o.id}" id="opt-${o.id}" onchange="selectSingle(${q.id},${o.id})"/>
          <span class="option-label">${escHtml(o.option_text)}</span>
        </label>`).join('') +
      `</div>`;
  } else if (type === 'multiple_choice') {
    optionsHTML = `<div class="options-list-student">` +
      q.options.map(o => `
        <label class="option-item" id="opt-label-${o.id}">
          <input type="checkbox" name="q${q.id}" value="${o.id}" id="opt-${o.id}" onchange="toggleMulti(${q.id},${o.id})"/>
          <span class="option-label">${escHtml(o.option_text)}</span>
        </label>`).join('') +
      `</div>`;
  } else if (type === 'image_choice') {
    optionsHTML = `<div class="options-list-student" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:1rem;">` +
      q.options.map(o => `
        <label class="option-item" id="opt-label-${o.id}" style="flex-direction:column;align-items:center;text-align:center;padding:1rem">
          <input type="radio" name="q${q.id}" value="${o.id}" id="opt-${o.id}" onchange="selectSingle(${q.id},${o.id})" style="position:absolute;opacity:0"/>
          ${o.image_url ? `<img src="${o.image_url}" class="test-image" style="max-height:150px" />` : ''}
          <span class="option-label" style="margin-top:0.5rem">${escHtml(o.option_text)}</span>
        </label>`).join('') +
      `</div>`;
  } else if (type === 'hotspot') {
    optionsHTML = `
      <div style="max-width:100%;overflow:auto;border:1px solid var(--border-light);border-radius:var(--radius-sm);">
        <div style="position:relative;display:inline-block;max-width:100%;">
          <img id="hotspot-img-${q.id}" src="${q.image_url}" style="max-width:100%;height:auto;cursor:crosshair;display:block;" onclick="handleHotspotClick(event, ${q.id})" onload="restoreHotspotMarker(${q.id})">
          <div id="hotspot-marker-${q.id}" style="position:absolute;width:20px;height:20px;background:rgba(255,0,0,0.6);border-radius:50%;transform:translate(-50%,-50%);display:none;pointer-events:none;z-index:10;box-shadow: 0 0 0 2px white, 0 0 4px rgba(0,0,0,0.5);"></div>
        </div>
      </div>
    `;
  } else if (type === 'short_text') {
    optionsHTML = `
      <input type="text" class="short-text-input" id="short-input-${q.id}"
        placeholder="Введіть відповідь..."
        oninput="debouncedSaveText(${q.id}, this.value)"
      />`;
  } else if (type === 'matching') {
    const rightSides = matchingPools[q.id] || [];
    const currentAnswers = answers[q.id] || {};
    const usedValues = Object.values(currentAnswers).filter(Boolean);
    const unmatchedRightSides = rightSides.filter(val => !usedValues.includes(val));

    optionsHTML = `
      <div id="matching-question-${q.id}" class="matching-container">
        <div class="matching-grid">
    ` +
    q.options.map(o => {
      const matchedValue = currentAnswers[o.id] ?? currentAnswers[String(o.id)];
      const isSelected = selectedMatchingCardInfo && selectedMatchingCardInfo.qId === q.id && selectedMatchingCardInfo.value === matchedValue;
      return `
        <div class="matching-row-student">
          <div class="matching-left">${escHtml(o.option_text)}</div>
          
          <div class="matching-drop-zone ${matchedValue ? 'has-item' : ''} ${selectedMatchingCardInfo && selectedMatchingCardInfo.qId === q.id ? 'zone-selectable' : ''}" 
               data-option-id="${o.id}"
               data-question-id="${q.id}"
               onclick="handleMatchingZoneClick(event, ${q.id}, ${o.id})">
            ${matchedValue ? `
              <div class="matching-draggable-card dragged-in-zone ${isSelected ? 'matching-card-selected' : ''}" 
                   data-value="${escHtml(matchedValue)}"
                   data-option-id="${o.id}"
                   data-question-id="${q.id}"
                   onclick="handleMatchingCardClick(event, ${q.id}, this)"
                   onpointerdown="startMatchingDrag(event, ${q.id}, ${o.id})">
                <span class="sequence-handle" title="Перетягнути">⠿</span>
                <span class="matching-card-text" style="flex:1; text-align:left; word-break:break-word;">${escHtml(matchedValue)}</span>
                <button type="button" class="matching-remove-btn" title="Скинути до списку" onclick="unmatchOption(event, ${q.id}, ${o.id})">✕</button>
              </div>
            ` : `
              <span class="matching-placeholder-text" style="font-size:0.85rem; color:var(--text-dim); text-align:center; pointer-events:none;">
                ${selectedMatchingCardInfo && selectedMatchingCardInfo.qId === q.id ? 'Натисніть сюди для розміщення' : 'Перетягніть або натисніть для вибору'}
              </span>
            `}
          </div>

          <select id="match-${o.id}" class="short-text-input matching-fallback-select" style="display:none; flex:1; margin:0;" onchange="saveMatching(${q.id}, ${o.id}, this.value)">
            <option value="">-- Оберіть --</option>
            ${rightSides.map(r => `<option value="${escHtml(r)}" ${matchedValue === r ? 'selected' : ''}>${escHtml(r)}</option>`).join('')}
          </select>
        </div>
      `;
    }).join('') +
    `
        </div>
        <div class="matching-pool-wrapper">
          <div class="matching-pool-title">Доступні варіанти (${unmatchedRightSides.length}):</div>
          <div class="matching-pool" id="matching-pool-${q.id}">
    ` +
    unmatchedRightSides.map(r => {
      const isSelected = selectedMatchingCardInfo && selectedMatchingCardInfo.qId === q.id && selectedMatchingCardInfo.value === r;
      return `
        <div class="matching-draggable-card ${isSelected ? 'matching-card-selected' : ''}" 
             data-value="${escHtml(r)}"
             data-question-id="${q.id}"
             onclick="handleMatchingCardClick(event, ${q.id}, this)"
             onpointerdown="startMatchingDrag(event, ${q.id}, null)">
          <span class="sequence-handle" title="Перетягнути">⠿</span>
          <span class="matching-card-text">${escHtml(r)}</span>
        </div>
      `;
    }).join('') +
    (unmatchedRightSides.length === 0 ? `<div class="matching-pool-empty" style="font-size:0.85rem; color:var(--success); text-align:center; padding:0.75rem;">Усі варіанти розподілено ✓</div>` : '') +
    `
          </div>
        </div>
      </div>
    `;
  } else if (type === 'sequence') {
    const rawOrder = answers[q.id] || sequenceOrders[q.id] || [];
    const currentOrderIds = Array.isArray(rawOrder) ? rawOrder.map(id => parseInt(id)) : [];
    let orderedOptions = currentOrderIds.map(id => q.options.find(o => o.id === id)).filter(Boolean);
    if (orderedOptions.length !== q.options.length) {
      orderedOptions = [...q.options];
      sequenceOrders[q.id] = orderedOptions.map(o => o.id);
    }
    
    optionsHTML = `<div class="options-list-student" id="sequence-list-${q.id}" style="display:flex;flex-direction:column;gap:0.5rem;position:relative;">` +
      orderedOptions.map((o, idx) => `
        <div class="option-item draggable-sequence-item" 
             data-id="${o.id}" 
             data-index="${idx}"
             data-question-id="${q.id}"
             onpointerdown="startSequenceDrag(event, ${q.id})"
             style="display:flex;align-items:center;gap:0.75rem;background:var(--bg-card2);cursor:grab;touch-action:none;user-select:none;">
          <span class="sequence-handle" title="Перетягнути">⠿</span>
          <span class="badge badge-count" style="width:28px;height:28px;min-width:28px;pointer-events:none;">${idx + 1}</span>
          <span style="flex:1;pointer-events:none;word-break:break-word;">${escHtml(o.option_text)}</span>
          <div style="display:flex;flex-direction:column;gap:0.25rem" onclick="event.stopPropagation();">
            <button type="button" class="btn btn-sm btn-secondary" style="padding:0.1rem 0.45rem" onclick="moveSequence(${q.id}, ${idx}, -1)" ${idx === 0 ? 'disabled' : ''}>▲</button>
            <button type="button" class="btn btn-sm btn-secondary" style="padding:0.1rem 0.45rem" onclick="moveSequence(${q.id}, ${idx}, 1)" ${idx === orderedOptions.length - 1 ? 'disabled' : ''}>▼</button>
          </div>
        </div>
      `).join('') +
      `</div>`;
  }

  const qImageHtml = (q.image_url && type !== 'hotspot') ? `<img src="${q.image_url}" class="test-image" />` : '';

  return `
    <div class="question-meta">
      <span class="q-number">Питання ${index + 1} з ${totalQ}</span>
      ${q.topic ? `<span class="q-topic">${escHtml(q.topic)}</span>` : ''}
      <span class="q-points">${q.points} б.</span>
    </div>
    <div class="question-text">${escHtml(q.question_text)}</div>
    ${hintText ? `<div class="question-hint" style="color: var(--text-muted); font-size: 0.85rem; margin-top: 0.25rem; margin-bottom: 1rem; font-style: italic;">💡 Підказка: ${hintText}</div>` : ''}
    ${qImageHtml}
    ${optionsHTML}
    <div class="save-indicator" id="save-indicator-${q.id}"></div>
  `;
}

// ---------------------------------------------------------------------------
// Відновлення відповідей
// ---------------------------------------------------------------------------
function restoreAnswer(q) {
  const saved = answers[q.id];
  if (saved === undefined || saved === null) return;
  const type = q.question_type;

  if (type === 'single_choice' || type === 'true_false' || type === 'image_choice') {
    const optId = Array.isArray(saved) ? saved[0] : saved;
    const radio = document.getElementById(`opt-${optId}`);
    if (radio) {
      radio.checked = true;
      document.getElementById(`opt-label-${optId}`)?.classList.add('selected');
    }
  } else if (type === 'multiple_choice') {
    const selected = Array.isArray(saved) ? saved : [];
    selected.forEach(optId => {
      const cb = document.getElementById(`opt-${optId}`);
      if (cb) {
        cb.checked = true;
        document.getElementById(`opt-label-${optId}`)?.classList.add('selected');
      }
    });
  } else if (type === 'short_text') {
    const input = document.getElementById(`short-input-${q.id}`);
    if (input) input.value = saved || '';
  } else if (type === 'matching') {
    const selectedPairs = typeof saved === 'object' ? saved : {};
    Object.entries(selectedPairs).forEach(([optId, matchVal]) => {
      const select = document.getElementById(`match-${optId}`);
      if (select) select.value = matchVal;
    });
    updateMatchingDropdowns(q.id);
  } else if (type === 'hotspot') {
    // Cannot draw marker immediately if image isn't loaded, so it's handled by onload
  }
}

// ---------------------------------------------------------------------------
// Відповіді — hotspot
// ---------------------------------------------------------------------------
function handleHotspotClick(event, qId) {
  const img = event.target;
  const rect = img.getBoundingClientRect();
  const scaleX = img.naturalWidth / rect.width;
  const scaleY = img.naturalHeight / rect.height;
  
  const clickX = (event.clientX - rect.left) * scaleX;
  const clickY = (event.clientY - rect.top) * scaleY;
  
  const marker = document.getElementById(`hotspot-marker-${qId}`);
  if (marker) {
    marker.style.left = (event.clientX - rect.left) + 'px';
    marker.style.top = (event.clientY - rect.top) + 'px';
    marker.style.display = 'block';
  }
  
  const val = { x: clickX, y: clickY };
  answers[qId] = val;
  saveAnswer(qId, null, val);
}

function restoreHotspotMarker(qId) {
  const saved = answers[qId];
  if (!saved || typeof saved !== 'object' || !('x' in saved)) return;
  
  const img = document.getElementById(`hotspot-img-${qId}`);
  const marker = document.getElementById(`hotspot-marker-${qId}`);
  if (!img || !marker) return;
  
  const rect = img.getBoundingClientRect();
  const scaleX = rect.width / img.naturalWidth;
  const scaleY = rect.height / img.naturalHeight;
  
  marker.style.left = (saved.x * scaleX) + 'px';
  marker.style.top = (saved.y * scaleY) + 'px';
  marker.style.display = 'block';
}

// ---------------------------------------------------------------------------
// Відповіді — single choice / image choice
// ---------------------------------------------------------------------------
function selectSingle(questionId, optionId) {
  const q = questions.find(q => q.id === questionId);
  if (!q) return;
  // знімаємо виділення з усіх
  q.options.forEach(o => document.getElementById(`opt-label-${o.id}`)?.classList.remove('selected'));
  document.getElementById(`opt-label-${optionId}`)?.classList.add('selected');
  const radio = document.getElementById(`opt-${optionId}`);
  if (radio) radio.checked = true;

  answers[questionId] = [optionId];
  saveAnswer(questionId, null, [optionId]);
}

// ---------------------------------------------------------------------------
// Відповіді — multiple choice
// ---------------------------------------------------------------------------
function toggleMulti(questionId, optionId) {
  const q = questions.find(q => q.id === questionId);
  if (!q) return;

  const current = Array.isArray(answers[questionId]) ? [...answers[questionId]] : [];
  const cb = document.getElementById(`opt-${optionId}`);
  const label = document.getElementById(`opt-label-${optionId}`);

  if (current.includes(optionId)) {
    answers[questionId] = current.filter(id => id !== optionId);
    if (cb) cb.checked = false;
    label?.classList.remove('selected');
  } else {
    answers[questionId] = [...current, optionId];
    if (cb) cb.checked = true;
    label?.classList.add('selected');
  }

  saveAnswer(questionId, null, answers[questionId]);
}

// ---------------------------------------------------------------------------
// Відповіді — short text
// ---------------------------------------------------------------------------
let _textSaveTimer = null;
function debouncedSaveText(questionId, value) {
  answers[questionId] = value;
  clearTimeout(_textSaveTimer);
  _textSaveTimer = setTimeout(() => saveAnswer(questionId, value, null), 800);
}

function updateMatchingDropdowns(questionId) {
  const currentAnswers = answers[questionId] || {};
  const selectedValues = Object.values(currentAnswers);
  const q = questions.find(q => q.id === questionId);
  if (!q) return;

  q.options.forEach(o => {
    const select = document.getElementById(`match-${o.id}`);
    if (!select) return;

    Array.from(select.options).forEach(opt => {
      if (opt.value === "") return;
      if (selectedValues.includes(opt.value) && select.value !== opt.value) {
        opt.disabled = true;
      } else {
        opt.disabled = false;
      }
    });
  });
}

function saveMatching(questionId, optionId, matchValue) {
  if (!answers[questionId]) answers[questionId] = {};
  if (matchValue === "") {
    delete answers[questionId][optionId];
  } else {
    // Remove duplicate assignments
    Object.keys(answers[questionId]).forEach(k => {
      if (answers[questionId][k] === matchValue && parseInt(k) !== parseInt(optionId)) {
        delete answers[questionId][k];
      }
    });
    answers[questionId][optionId] = matchValue;
  }
  
  updateMatchingDropdowns(questionId);
  saveAnswer(questionId, null, answers[questionId]);
}

function unmatchOption(event, questionId, optionId) {
  if (event) event.stopPropagation();
  if (answers[questionId]) {
    delete answers[questionId][optionId];
    saveAnswer(questionId, null, answers[questionId]);
    renderQuestion(currentIndex);
  }
}

// ---------------------------------------------------------------------------
// Відповіді — sequence
// ---------------------------------------------------------------------------
function moveSequence(questionId, idx, dir) {
  const q = questions.find(q => q.id === questionId);
  if (!q) return;
  const currentOrder = answers[questionId] || sequenceOrders[questionId] || q.options.map(o => o.id);
  const orderList = Array.isArray(currentOrder) ? [...currentOrder.map(id => parseInt(id))] : q.options.map(o => o.id);
  const newIdx = idx + dir;
  if (newIdx < 0 || newIdx >= orderList.length) return;
  
  [orderList[idx], orderList[newIdx]] = [orderList[newIdx], orderList[idx]];
  answers[questionId] = orderList;
  sequenceOrders[questionId] = orderList;
  
  renderQuestion(currentIndex);
  saveAnswer(questionId, null, orderList);
}

// ---------------------------------------------------------------------------
// AJAX: зберегти відповідь
// ---------------------------------------------------------------------------
async function saveAnswer(questionId, answerText, selectedOptions) {
  const indicator = document.getElementById(`save-indicator-${questionId}`);
  if (indicator) {
    indicator.textContent = 'Збереження...';
    indicator.className = 'save-indicator show';
  }

  validateAnswer();

  try {
    const resp = await fetch(`/student/test/${ATTEMPT_ID}/save-answer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question_id: questionId, answer_text: answerText, selected_options: selectedOptions }),
    });
    if (resp.ok) {
      if (indicator) { indicator.textContent = 'Збережено ✓'; indicator.className = 'save-indicator saved'; }
      skippedQuestions.delete(questionId);
      updateNavigator();
      updateAnsweredCount();
      if (isOffline) goOnline();
    } else {
      if (indicator) { indicator.textContent = 'Помилка збереження'; indicator.className = 'save-indicator error'; }
      if (resp.status >= 500) {
        goOffline({ questionId, answerText, selectedOptions });
      }
    }
  } catch {
    if (indicator) { indicator.textContent = 'Немає з\'єднання'; indicator.className = 'save-indicator error'; }
    goOffline({ questionId, answerText, selectedOptions });
  }
}

// ---------------------------------------------------------------------------
// Навігація між питаннями
// ---------------------------------------------------------------------------
function skipQuestion() {
  const q = questions[currentIndex];
  // Позначаємо як пропущене
  skippedQuestions.add(q.id);
  logBrowserEvent('question_skipped', `question_id=${q.id}`);
  // Видаляємо поточний елемент з черги та додаємо в кінець (якщо є що додавати)
  const currentEntry = questionQueue[currentQueueIdx];
  questionQueue.splice(currentQueueIdx, 1);
  questionQueue.push(currentEntry);
  // Рендеримо наступне питання в черзі (за тим самим індексом)
  if (currentQueueIdx >= questionQueue.length) {
    // Якщо пропустили останнє — фактично всі пропущені, переходимо на перше
    renderFromQueue(0);
  } else {
    renderFromQueue(currentQueueIdx);
  }
}

function nextQuestion(force = false) {
  if (!force) {
    const qId = questions[currentIndex]?.id;
    if (!hasAnswer(qId)) return;
  }
  // Якщо відповів — більше не вважається пропущеним
  skippedQuestions.delete(questions[currentIndex]?.id);

  const nextIdx = currentQueueIdx + 1;
  if (nextIdx >= questionQueue.length) {
    finishTest();
  } else {
    renderFromQueue(nextIdx);
  }
}

function goToQuestion(i) {
  // Залишаємо для сумісності, але навігатор не клікабельний
  if (questions[i] && lockedQuestions.has(questions[i].id)) return;
  renderQuestion(i);
}

function updateAnsweredCount() {
  let count = 0;
  questions.forEach(q => {
    if (hasAnswer(q.id)) count++;
  });
  const total = document.getElementById('answered-count');
  if (total) total.textContent = count;
}

// ---------------------------------------------------------------------------
// Таймер
// ---------------------------------------------------------------------------
function startTimer() {
  if (timeLeft !== null) {
    updateTimerDisplay();
    timerInterval = setInterval(() => {
      if (isPaused || isOffline) return;
      timeLeft--;
      if (timeLeft <= 0) {
        clearInterval(timerInterval);
        timeLeft = 0;
        updateTimerDisplay();
        autoSubmitTimeout();
      } else {
        updateTimerDisplay();
      }
    }, 1000);
  }
}

function resetQTimer() {
  if (TEST_DATA.time_limit_per_question) {
    if (qTimerInterval) clearInterval(qTimerInterval);
    qTimeLeft = TEST_DATA.time_limit_per_question;
    updateQTimerDisplay();
    qTimerInterval = setInterval(() => {
      if (isPaused || isOffline) return;
      qTimeLeft--;
      if (qTimeLeft <= 0) {
        clearInterval(qTimerInterval);
        qTimeLeft = 0;
        updateQTimerDisplay();

        const q = questions[currentIndex];
        lockedQuestions.add(q.id);

        if (!hasAnswer(q.id)) {
          // Помічаємо як закінчився час (сірий)
          timedOutQuestions.add(q.id);
          answers[q.id] = "";
          saveAnswer(q.id, "", null);
        }

        nextQuestion(true); // force to next in queue
      } else {
        updateQTimerDisplay();
      }
    }, 1000);
  }
}

function updateQTimerDisplay() {
  const disp = document.getElementById('q-timer-display');
  const box  = document.getElementById('q-timer-box');
  if (!disp || !box) return;

  const m = Math.floor(qTimeLeft / 60);
  const s = qTimeLeft % 60;
  const p = v => String(v).padStart(2, '0');
  disp.textContent = `${p(m)}:${p(s)}`;

  if (qTimeLeft <= 15) {
    box.classList.add('blinking-red');
  } else {
    box.classList.remove('blinking-red');
  }
}

function updateTimerDisplay() {
  if (timeLeft === null) return;
  const disp = document.getElementById('timer-display');
  const box  = document.getElementById('timer-box');
  if (!disp) return;

  const h = Math.floor(timeLeft / 3600);
  const m = Math.floor((timeLeft % 3600) / 60);
  const s = timeLeft % 60;
  const p = v => String(v).padStart(2, '0');

  disp.textContent = h > 0 ? `${h}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`;

  if (timeLeft <= 300) box.classList.add('timer-warning'); // < 5 min
  if (timeLeft <= 60)  box.classList.add('timer-danger');  // < 1 min
}

function autoSubmitTimeout() {
  alert('Час вийшов! Відповіді зберігаються автоматично.');
  logBrowserEvent('timeout_auto_submit');
  confirmFinish();
}

// ---------------------------------------------------------------------------
// Завершення тесту та Повноекранний режим
// ---------------------------------------------------------------------------

let testFinished = false;

function startFullscreenTest() {
  document.getElementById('start-overlay').style.display = 'none';
  const elem = document.documentElement;
  
  const handleError = (err) => {
    console.warn('Fullscreen request failed:', err);
    alert('Не вдалося увімкнути повноекранний режим. Деякі функції можуть працювати некоректно. Надайте дозвіл браузеру.');
  };

  if (elem.requestFullscreen) {
    elem.requestFullscreen().catch(handleError);
  } else if (elem.webkitRequestFullscreen) {
    elem.webkitRequestFullscreen().catch(handleError);
  }
}

function returnToFullscreen() {
  const elem = document.documentElement;
  
  const handleSuccess = () => {
    document.getElementById('fullscreen-block').style.display = 'none';
  };
  
  const handleError = (err) => {
    console.warn('Fullscreen request failed:', err);
    document.getElementById('fullscreen-block').style.display = 'flex';
    alert('Не вдалося увімкнути повноекранний режим. Спробуйте ще раз або надайте дозвіл браузеру.');
  };

  if (elem.requestFullscreen) {
    elem.requestFullscreen().then(handleSuccess).catch(handleError);
  } else if (elem.webkitRequestFullscreen) {
    elem.webkitRequestFullscreen().then(handleSuccess).catch(handleError);
  } else {
    // Якщо браузер не підтримує API
    handleSuccess(); 
  }
}

let fsTimerInterval = null;
let fsTimeLeft = 10;

function startFullscreenReturnTimer() {
  if (fsTimerInterval) return; // already running
  fsTimeLeft = 10;
  const timerEl = document.getElementById('fs-timer-display');
  const violationTimerEl = document.getElementById('violation-timer-display');
  if (timerEl) timerEl.textContent = fsTimeLeft;
  if (violationTimerEl) violationTimerEl.textContent = fsTimeLeft;
  
  logBrowserEvent('fullscreen_exit');
  
  fsTimerInterval = setInterval(() => {
    if (isPaused || isOffline) return; // don't count down if paused
    fsTimeLeft--;
    if (timerEl) timerEl.textContent = fsTimeLeft;
    if (violationTimerEl) violationTimerEl.textContent = fsTimeLeft;
    
    if (fsTimeLeft <= 0) {
      logBrowserEvent('fullscreen_timeout_fail');
      
      const q = questions[currentIndex];
      if (q && !lockedQuestions.has(q.id)) {
        lockedQuestions.add(q.id);
        timedOutQuestions.add(q.id);
        answers[q.id] = "";
        saveAnswer(q.id, "", null);
      }
      
      const isLastQuestion = (currentQueueIdx + 1 >= questionQueue.length);
      
      nextQuestion(true);
      
      if (isLastQuestion) {
        clearInterval(fsTimerInterval);
        fsTimerInterval = null;
      } else {
        fsTimeLeft = 10;
        if (timerEl) timerEl.textContent = fsTimeLeft;
        if (violationTimerEl) violationTimerEl.textContent = fsTimeLeft;
      }
    }
  }, 1000);
}

function stopFullscreenReturnTimer() {
  if (fsTimerInterval) {
    clearInterval(fsTimerInterval);
    fsTimerInterval = null;
    logBrowserEvent('fullscreen_return', `time_left=${fsTimeLeft}`);
  } else {
    // If timer wasn't running (e.g. initial start, or already timed out)
    logBrowserEvent('fullscreen_return');
  }
}

document.addEventListener('fullscreenchange', () => {
  if (testFinished) return;
  if (!document.fullscreenElement) {
    document.getElementById('fullscreen-block').style.display = 'flex';
    startFullscreenReturnTimer();
  } else {
    document.getElementById('fullscreen-block').style.display = 'none';
    stopFullscreenReturnTimer();
  }
});

document.addEventListener('webkitfullscreenchange', () => {
  if (testFinished) return;
  if (!document.webkitFullscreenElement) {
    document.getElementById('fullscreen-block').style.display = 'flex';
    startFullscreenReturnTimer();
  } else {
    document.getElementById('fullscreen-block').style.display = 'none';
    stopFullscreenReturnTimer();
  }
});

function finishTest() {
  document.getElementById('finish-modal').style.display = 'flex';
}
function closeFinishModal() {
  document.getElementById('finish-modal').style.display = 'none';
}

async function confirmFinish() {
  testFinished = true; // Prevent violations during redirect
  document.getElementById('confirm-finish-btn').disabled = true;
  document.getElementById('confirm-finish-btn').textContent = 'Завершення...';

  try {
    const res = await fetch(`/student/test/${ATTEMPT_ID}/finish`, { method: 'POST' });
    if (res.ok) {
      window.location.href = `/student/test/${ATTEMPT_ID}/finished`;
    } else {
      alert('Помилка при завершенні. Спробуйте ще раз.');
      document.getElementById('confirm-finish-btn').disabled = false;
      document.getElementById('confirm-finish-btn').textContent = 'Завершити';
    }
  } catch {
    goOffline();
    document.getElementById('confirm-finish-btn').disabled = false;
    document.getElementById('confirm-finish-btn').textContent = 'Завершити';
  }
}

// ---------------------------------------------------------------------------
// Логування подій (перемикання вкладок тощо)
// ---------------------------------------------------------------------------
function setupBrowserEvents() {
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      handleViolation();
    } else {
      logBrowserEvent('tab_focus');
    }
  });
  window.addEventListener('blur', () => {
    // Fallback for some window switching
    if (!document.hidden) {
      handleViolation();
    }
  });
  window.addEventListener('focus', () => {
    if (!document.hidden) logBrowserEvent('tab_focus');
  });
  window.addEventListener('offline', () => {
    logBrowserEvent('connection_lost');
    goOffline();
  });
  window.addEventListener('online', () => {
    logBrowserEvent('reconnect');
  });
  
  // Anti-cheat: Block copy, cut, right-click, selectstart, dragstart
  document.addEventListener('copy', (e) => e.preventDefault());
  document.addEventListener('cut', (e) => e.preventDefault());
  document.addEventListener('contextmenu', (e) => e.preventDefault());
  document.addEventListener('dragstart', (e) => e.preventDefault());
  document.addEventListener('selectstart', (e) => {
    const tag = e.target.tagName.toLowerCase();
    if (tag !== 'input' && tag !== 'textarea') {
      e.preventDefault();
    }
  });
}

function handleViolation() {
  if (testFinished || isViolationShowing) return;
  violations++;
  isViolationShowing = true;
  
  logBrowserEvent('tab_blur', `Попередження ${violations}/3`);
  startFullscreenReturnTimer();
  
  if (violations >= MAX_VIOLATIONS) {
    alert("Критичне порушення правил тестування! Тест буде автоматично завершено.");
    confirmFinish();
  } else {
    document.getElementById('violation-count').textContent = violations;
    document.getElementById('violation-block').style.display = 'flex';
  }
}

function dismissViolationModal() {
  document.getElementById('violation-block').style.display = 'none';
  isViolationShowing = false;
  stopFullscreenReturnTimer();
  returnToFullscreen();
}

function logBrowserEvent(eventType, details = null) {
  fetch(`/student/event/${ATTEMPT_ID}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ event_type: eventType, details: details })
  }).catch(() => {});
}

function escHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ---------------------------------------------------------------------------
// Student websocket notifications & fallback polling
// ---------------------------------------------------------------------------
let studentWs = null;
function connectStudentWebSocket() {
  if (testFinished) return;
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${protocol}//${location.host}/ws/student/${ATTEMPT_ID}`;

  studentWs = new WebSocket(url);

  studentWs.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.event === 'pause') {
        handlePauseEvent();
      } else if (data.event === 'resume') {
        handleResumeEvent();
      } else if (data.event === 'stop') {
        handleStopEvent();
      }
    } catch (e) {}
  };

  studentWs.onclose = () => {
    if (!testFinished) {
      setTimeout(connectStudentWebSocket, 2000);
    }
  };
}

function handlePauseEvent() {
  isPaused = true;
  const pb = document.getElementById('pause-block');
  if (pb) pb.style.display = 'flex';
}

function handleResumeEvent() {
  isPaused = false;
  const pb = document.getElementById('pause-block');
  if (pb) pb.style.display = 'none';
}

function handleStopEvent() {
  testFinished = true;
  if (timerInterval) clearInterval(timerInterval);
  if (qTimerInterval) clearInterval(qTimerInterval);
  alert('Проходження тесту завершено вчителем.');
  window.location.href = `/student/test/${ATTEMPT_ID}/finished`;
}

async function fallbackCheckStatus() {
  if (testFinished || isOffline) return;
  try {
    const res = await fetch(`/api/attempt/${ATTEMPT_ID}`);
    if (!res.ok) return;
    const data = await res.json();
    if (data.status === 'paused') {
      if (!isPaused) handlePauseEvent();
    } else if (data.status === 'stopped' || data.status === 'finished' || data.status === 'timeout') {
      handleStopEvent();
    } else if (data.status === 'in_progress') {
      if (isPaused) handleResumeEvent();
    }
  } catch (e) {
    goOffline();
  }
}

// ---------------------------------------------------------------------------
// Офлайн режим та відновлення зв'язку
// ---------------------------------------------------------------------------
function goOffline(failedAnswer = null) {
  if (isOffline) return;
  isOffline = true;
  if (failedAnswer) {
    lastFailedAnswer = failedAnswer;
  }
  const ob = document.getElementById('offline-block');
  if (ob) ob.style.display = 'flex';
  
  if (studentWs) {
    try { studentWs.close(); } catch(e) {}
  }
  startOfflinePing();
}

function goOnline() {
  if (!isOffline) return;
  isOffline = false;
  const ob = document.getElementById('offline-block');
  if (ob) ob.style.display = 'none';
  
  connectStudentWebSocket();
  
  // Повторно надсилаємо відповідь, яка не збереглася через збій
  if (lastFailedAnswer) {
    const { questionId, answerText, selectedOptions } = lastFailedAnswer;
    lastFailedAnswer = null;
    saveAnswer(questionId, answerText, selectedOptions);
  }
}

function startOfflinePing() {
  const interval = setInterval(async () => {
    if (!isOffline) {
      clearInterval(interval);
      return;
    }
    try {
      const res = await fetch(`/api/attempt/${ATTEMPT_ID}`, { cache: 'no-store' });
      if (res.ok) {
        clearInterval(interval);
        goOnline();
      }
    } catch(e) {
      // Все ще офлайн
    }
  }, 3000);
}

// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// DRAG & DROP ENGINE (Sequence & Matching)
// ---------------------------------------------------------------------------

// --- SEQUENCE DRAG & DROP ---
let seqDragState = null;

function startSequenceDrag(e, qId) {
  if (e.target.closest('button')) return;
  if (e.button !== 0 && e.pointerType !== 'touch') return;

  const item = e.target.closest('.draggable-sequence-item');
  if (!item) return;

  const listContainer = item.parentElement;
  if (!listContainer) return;

  const rect = item.getBoundingClientRect();
  const offsetX = e.clientX - rect.left;
  const offsetY = e.clientY - rect.top;

  // Create floating ghost overlay
  const ghost = item.cloneNode(true);
  ghost.className = 'draggable-sequence-item sequence-drag-ghost';
  ghost.style.width = `${rect.width}px`;
  ghost.style.height = `${rect.height}px`;
  ghost.style.left = `${rect.left}px`;
  ghost.style.top = `${rect.top}px`;
  document.body.appendChild(ghost);

  item.classList.add('dragging');

  seqDragState = {
    qId: qId,
    item: item,
    ghost: ghost,
    listContainer: listContainer,
    offsetX: offsetX,
    offsetY: offsetY,
    pointerId: e.pointerId,
    hasMoved: false,
  };

  window.addEventListener('pointermove', onSequencePointerMove, { passive: false });
  window.addEventListener('pointerup', onSequencePointerUp, { passive: false });
  window.addEventListener('pointercancel', onSequencePointerUp, { passive: false });
}

function onSequencePointerMove(e) {
  if (!seqDragState) return;
  e.preventDefault();
  seqDragState.hasMoved = true;

  const { ghost, item, listContainer, offsetX, offsetY } = seqDragState;
  
  const currentX = e.clientX - offsetX;
  const currentY = e.clientY - offsetY;
  ghost.style.left = `${currentX}px`;
  ghost.style.top = `${currentY}px`;

  // Find target position among items
  const siblings = Array.from(listContainer.querySelectorAll('.draggable-sequence-item:not(.sequence-drag-ghost)'));
  const cursorY = e.clientY;

  for (let sib of siblings) {
    if (sib === item) continue;
    const sibRect = sib.getBoundingClientRect();
    const sibMiddleY = sibRect.top + sibRect.height / 2;

    if (cursorY < sibMiddleY) {
      if (item.nextSibling !== sib) {
        listContainer.insertBefore(item, sib);
      }
      break;
    } else if (cursorY > sibMiddleY) {
      if (item.previousSibling !== sib) {
        listContainer.insertBefore(item, sib.nextSibling);
      }
    }
  }

  // Update live badge numbers in real-time
  const liveItems = Array.from(listContainer.querySelectorAll('.draggable-sequence-item:not(.sequence-drag-ghost)'));
  liveItems.forEach((el, idx) => {
    const badge = el.querySelector('.badge-count');
    if (badge) badge.textContent = idx + 1;
    const upBtn = el.querySelector('button:first-child');
    const downBtn = el.querySelector('button:last-child');
    if (upBtn) upBtn.disabled = idx === 0;
    if (downBtn) downBtn.disabled = idx === liveItems.length - 1;
  });
}

function onSequencePointerUp(e) {
  if (!seqDragState) return;
  window.removeEventListener('pointermove', onSequencePointerMove);
  window.removeEventListener('pointerup', onSequencePointerUp);
  window.removeEventListener('pointercancel', onSequencePointerUp);

  const { qId, item, ghost, listContainer, hasMoved } = seqDragState;
  
  if (ghost && ghost.parentNode) ghost.parentNode.removeChild(ghost);
  if (item) item.classList.remove('dragging');

  if (hasMoved && listContainer) {
    const liveItems = Array.from(listContainer.querySelectorAll('.draggable-sequence-item:not(.sequence-drag-ghost)'));
    const newOrderIds = liveItems.map(el => parseInt(el.dataset.id)).filter(id => !isNaN(id));
    
    answers[qId] = newOrderIds;
    sequenceOrders[qId] = newOrderIds;

    saveAnswer(qId, null, newOrderIds);
    renderQuestion(currentIndex);
  }

  seqDragState = null;
}


// --- MATCHING DRAG & DROP & CLICK ---
let matchingDragState = null;
let selectedMatchingCardInfo = null; // { qId, value }

function startMatchingDrag(e, qId, fromOptionId) {
  if (e.target.closest('button')) return;
  if (e.button !== 0 && e.pointerType !== 'touch') return;

  const card = e.target.closest('.matching-draggable-card');
  if (!card) return;

  const rect = card.getBoundingClientRect();
  const startX = e.clientX;
  const startY = e.clientY;
  const matchValue = card.dataset.value;

  matchingDragState = {
    qId: qId,
    fromOptionId: fromOptionId,
    card: card,
    value: matchValue,
    startX: startX,
    startY: startY,
    offsetX: startX - rect.left,
    offsetY: startY - rect.top,
    rect: rect,
    ghost: null,
    isDragging: false,
  };

  window.addEventListener('pointermove', onMatchingPointerMove, { passive: false });
  window.addEventListener('pointerup', onMatchingPointerUp, { passive: false });
  window.addEventListener('pointercancel', onMatchingPointerUp, { passive: false });
}

function onMatchingPointerMove(e) {
  if (!matchingDragState) return;

  const dx = e.clientX - matchingDragState.startX;
  const dy = e.clientY - matchingDragState.startY;

  // Threshold check to distinguish click vs drag
  if (!matchingDragState.isDragging && Math.hypot(dx, dy) > 6) {
    matchingDragState.isDragging = true;
    
    // Create ghost
    const ghost = matchingDragState.card.cloneNode(true);
    ghost.className = 'matching-draggable-card matching-drag-ghost';
    ghost.style.width = `${matchingDragState.rect.width}px`;
    ghost.style.left = `${matchingDragState.rect.left}px`;
    ghost.style.top = `${matchingDragState.rect.top}px`;
    
    // Remove close button from ghost if present
    const removeBtn = ghost.querySelector('.matching-remove-btn');
    if (removeBtn) removeBtn.remove();
    
    document.body.appendChild(ghost);
    matchingDragState.ghost = ghost;
    matchingDragState.card.classList.add('dragging');
  }

  if (matchingDragState.isDragging && matchingDragState.ghost) {
    e.preventDefault();
    const currentX = e.clientX - matchingDragState.offsetX;
    const currentY = e.clientY - matchingDragState.offsetY;
    matchingDragState.ghost.style.left = `${currentX}px`;
    matchingDragState.ghost.style.top = `${currentY}px`;

    // Drop zone hover detection
    const elementsUnder = document.elementsFromPoint(e.clientX, e.clientY);
    const dropZone = elementsUnder.find(el => el.classList && el.classList.contains('matching-drop-zone'));

    document.querySelectorAll('.matching-drop-zone.hovered').forEach(z => {
      if (z !== dropZone) z.classList.remove('hovered');
    });

    if (dropZone) {
      dropZone.classList.add('hovered');
    }
  }
}

function onMatchingPointerUp(e) {
  if (!matchingDragState) return;

  window.removeEventListener('pointermove', onMatchingPointerMove);
  window.removeEventListener('pointerup', onMatchingPointerUp);
  window.removeEventListener('pointercancel', onMatchingPointerUp);

  const { qId, fromOptionId, card, value, ghost, isDragging } = matchingDragState;

  document.querySelectorAll('.matching-drop-zone.hovered').forEach(z => z.classList.remove('hovered'));

  if (ghost && ghost.parentNode) ghost.parentNode.removeChild(ghost);
  if (card) card.classList.remove('dragging');

  if (isDragging) {
    // Find target drop zone
    const elementsUnder = document.elementsFromPoint(e.clientX, e.clientY);
    const dropZone = elementsUnder.find(el => el.classList && el.classList.contains('matching-drop-zone'));

    if (!answers[qId]) answers[qId] = {};

    if (dropZone) {
      const targetOptionId = parseInt(dropZone.dataset.optionId);

      // Clean up previous occurrences of this value
      Object.keys(answers[qId]).forEach(optId => {
        if (answers[qId][optId] === value) {
          delete answers[qId][optId];
        }
      });

      // Assign to target zone
      answers[qId][targetOptionId] = value;
    } else if (fromOptionId) {
      // If dragged out of a zone and dropped anywhere outside -> unmatch!
      delete answers[qId][fromOptionId];
    }

    selectedMatchingCardInfo = null;
    saveMatching(qId, fromOptionId, answers[qId][fromOptionId] || "");
    renderQuestion(currentIndex);
  }

  matchingDragState = null;
}

function handleMatchingCardClick(e, qId, element) {
  if (matchingDragState && matchingDragState.isDragging) return;
  e.stopPropagation();

  const matchValue = element.dataset.value;

  if (selectedMatchingCardInfo && selectedMatchingCardInfo.qId === qId && selectedMatchingCardInfo.value === matchValue) {
    selectedMatchingCardInfo = null;
  } else {
    selectedMatchingCardInfo = { qId: qId, value: matchValue };
  }
  renderQuestion(currentIndex);
}

function handleMatchingZoneClick(e, qId, optionId) {
  if (matchingDragState && matchingDragState.isDragging) return;

  if (!selectedMatchingCardInfo || selectedMatchingCardInfo.qId !== qId) return;

  const matchValue = selectedMatchingCardInfo.value;
  if (!answers[qId]) answers[qId] = {};

  // Remove value from any previously assigned zone
  Object.keys(answers[qId]).forEach(optId => {
    if (answers[qId][optId] === matchValue) {
      delete answers[qId][optId];
    }
  });

  // Assign to this zone
  answers[qId][optionId] = matchValue;
  selectedMatchingCardInfo = null;

  saveMatching(qId, optionId, matchValue);
  renderQuestion(currentIndex);
}

