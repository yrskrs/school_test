/**
 * teacher.js — Редактор питань для сторінок create_test.html / edit_test.html
 */

let questions = [];
let questionCounter = 0;
let uploadSessionId = null;

// ============================================================
// Історія дій (Undo / Redo) та Динамічна валідація помилок
// ============================================================
let historyStack = [];
let redoStack = [];
const MAX_HISTORY = 50;
let isHistoryAction = false;
let validationDebounceTimer = null;
let textInputHistoryTimer = null;

function saveHistoryState() {
  if (isHistoryAction) return;
  try {
    historyStack.push(JSON.stringify({
      questions: questions,
      counter: questionCounter
    }));
    if (historyStack.length > MAX_HISTORY) {
      historyStack.shift();
    }
    redoStack = [];
    updateUndoRedoButtons();
  } catch (e) {
    console.error('History save error', e);
  }
}

function updateUndoRedoButtons() {
  const btnUndo = document.getElementById('btn-undo');
  const btnRedo = document.getElementById('btn-redo');
  if (btnUndo) {
    btnUndo.disabled = historyStack.length === 0;
    btnUndo.style.opacity = historyStack.length === 0 ? '0.5' : '1';
    btnUndo.style.cursor = historyStack.length === 0 ? 'not-allowed' : 'pointer';
  }
  if (btnRedo) {
    btnRedo.disabled = redoStack.length === 0;
    btnRedo.style.opacity = redoStack.length === 0 ? '0.5' : '1';
    btnRedo.style.cursor = redoStack.length === 0 ? 'not-allowed' : 'pointer';
  }
}

window.undoLastAction = function() {
  if (historyStack.length === 0) return;
  isHistoryAction = true;
  try {
    redoStack.push(JSON.stringify({
      questions: questions,
      counter: questionCounter
    }));
    const raw = historyStack.pop();
    const state = JSON.parse(raw);
    questions = state.questions;
    questionCounter = state.counter;
    renderAllQuestionsFromState();
    scheduleValidation(50);
    updateUndoRedoButtons();
    if (window.showToast) {
      window.showToast('info', 'Дію скасовано ↶');
    }
  } catch (e) {
    console.error('Undo error', e);
  } finally {
    isHistoryAction = false;
  }
};

window.redoLastAction = function() {
  if (redoStack.length === 0) return;
  isHistoryAction = true;
  try {
    historyStack.push(JSON.stringify({
      questions: questions,
      counter: questionCounter
    }));
    const raw = redoStack.pop();
    const state = JSON.parse(raw);
    questions = state.questions;
    questionCounter = state.counter;
    renderAllQuestionsFromState();
    scheduleValidation(50);
    updateUndoRedoButtons();
    if (window.showToast) {
      window.showToast('info', 'Дію повернено ↷');
    }
  } catch (e) {
    console.error('Redo error', e);
  } finally {
    isHistoryAction = false;
  }
};

function renderAllQuestionsFromState() {
  const container = document.getElementById('questions-container');
  if (!container) return;
  container.innerHTML = `
    <div class="empty-questions" id="empty-questions-msg" style="display:${questions.length === 0 ? 'block' : 'none'}">
      <p>Питань ще немає. Натисніть «Додати питання».</p>
    </div>
  `;
  const isGrouped = document.getElementById('toggle-grouping') && document.getElementById('toggle-grouping').checked;
  if (isGrouped) {
    regroupQuestions();
  } else {
    questions.forEach(q => {
      const el = buildQuestionEl(q);
      container.appendChild(el);
      if (q.question_type === 'hotspot' && q.image_url) {
        initHotspotEditor(q._id);
      }
    });
  }
  updateEmptyMsg();
  updateCounter();
  renderTopicsManagement();
}

function scheduleValidation(delay = 300) {
  if (validationDebounceTimer) clearTimeout(validationDebounceTimer);
  validationDebounceTimer = setTimeout(() => {
    validateCurrentTestProblems();
  }, delay);
}

window.isEditingTest = true;

function validateCurrentTestProblems() {
  window.isEditingTest = true;
  const problems = [];
  const testTitleInput = document.getElementById('title');
  const testTitle = testTitleInput ? testTitleInput.value : 'Поточний тест';

  // Clear previous inline visual banners
  document.querySelectorAll('.question-warning-banner').forEach(b => b.remove());

  // 1. Перевірка дублювання варіантів відповідей у межах одного питання
  questions.forEach((q, idx) => {
    const qNum = idx + 1;
    if (q.options && q.options.length > 0) {
      const optMap = {};
      q.options.forEach(o => {
        const optTxt = (o.option_text || '').trim().toLowerCase();
        if (optTxt) {
          if (!optMap[optTxt]) optMap[optTxt] = [];
          optMap[optTxt].push(o);
        }
      });
      for (const [optTxt, optList] of Object.entries(optMap)) {
        if (optList.length > 1) {
          const shortOpt = optTxt.length > 30 ? optTxt.slice(0, 30) + '...' : optTxt;
          problems.push({
            target_id: q.db_id || q._id,
            question_id: q.db_id || q._id,
            question_num: `Питання №${qNum}`,
            question_idx: `Питання №${qNum} (з ${questions.length})`,
            question_text: q.question_text || 'Без тексту',
            test_title: testTitle,
            problem_type: 'Дублювання варіантів відповідей',
            problem_detail: `Варіант відповіді «${shortOpt}» зустрічається ${optList.length} рази у Ппитанні №${qNum}`,
            message: `Питання №${qNum}: Дублювання варіанту «${shortOpt}»`,
            link: `#question-${q.db_id || q._id}`
          });
        }
      }

      // 3. Matching right parts duplicate
      if (q.question_type === 'matching') {
        const matchMap = {};
        q.options.forEach(o => {
          const mTxt = (o.matching_text || '').trim().toLowerCase();
          if (mTxt) {
            if (!matchMap[mTxt]) matchMap[mTxt] = [];
            matchMap[mTxt].push(o);
          }
        });
        for (const [mTxt, matchList] of Object.entries(matchMap)) {
          if (matchList.length > 1) {
            const shortMatch = mTxt.length > 30 ? mTxt.slice(0, 30) + '...' : mTxt;
            problems.push({
              target_id: q.db_id || q._id,
              question_id: q.db_id || q._id,
              question_num: `Питання №${qNum}`,
              question_idx: `Питання №${qNum} (з ${questions.length})`,
              question_text: q.question_text || 'Без тексту',
              test_title: testTitle,
              problem_type: 'Дублювання відповідностей',
              problem_detail: `Права частина «${shortMatch}» повторюється ${matchList.length} рази у Ппитанні №${qNum}`,
              message: `Питання №${qNum}: Дублювання відповідності «${shortMatch}»`,
              link: `#question-${q.db_id || q._id}`
            });
          }
        }
      }
    }
  });

  // Inject visual warning banners inside question cards directly in DOM
  problems.forEach(p => {
    const qBlock = document.getElementById(`qblock-${p.target_id}`);
    if (qBlock) {
      const body = qBlock.querySelector('.question-editor-body');
      if (body) {
        const banner = document.createElement('div');
        banner.className = `question-warning-banner`;
        banner.style.background = 'var(--warning-bg)';
        banner.style.border = '1px solid var(--warning)';
        banner.style.color = 'var(--warning)';
        banner.style.fontSize = '0.85rem';
        banner.style.fontWeight = '600';
        banner.style.padding = '0.55rem 0.85rem';
        banner.style.borderRadius = 'var(--radius-sm)';
        banner.style.marginBottom = '0.75rem';
        banner.style.display = 'flex';
        banner.style.alignItems = 'center';
        banner.style.gap = '0.5rem';
        banner.innerHTML = `⚠️ <span>${escHtml(p.problem_detail)}</span>`;
        body.insertBefore(banner, body.firstChild);
      }
    }
  });

  window.currentEditorProblems = problems;

  // Live navbar notification dropdown update
  const badge = document.getElementById('notifications-badge');
  const list = document.getElementById('notifications-list');
  if (!badge || !list) return;

  if (problems.length > 0) {
    badge.style.display = 'block';
    badge.textContent = problems.length;
    list.innerHTML = '';

    // Quick open detailed modal button at top of dropdown
    const topBar = document.createElement('div');
    topBar.style.padding = '0.3rem 0.4rem 0.5rem 0.4rem';
    topBar.style.borderBottom = '1px solid var(--border)';
    topBar.style.marginBottom = '0.4rem';
    topBar.innerHTML = `<button type="button" onclick="openProblemsModal()" class="btn btn-sm btn-primary" style="width:100%; font-size:0.8rem; padding:0.35rem 0.5rem; display:flex; align-items:center; justify-content:center; gap:0.4rem;">
      🔍 Детальний перегляд та виправлення (${problems.length})
    </button>`;
    list.appendChild(topBar);

    problems.forEach(p => {
      const item = document.createElement('a');
      item.href = p.link;
      item.className = 'notification-item';
      item.style.textAlign = 'left';
      item.style.whiteSpace = 'normal';
      item.style.fontSize = '0.85rem';
      item.style.padding = '0.6rem 0.75rem';
      item.style.borderRadius = 'var(--radius-sm)';
      item.style.background = 'var(--bg-card2)';
      item.style.border = '1px solid var(--border-light)';
      item.style.borderLeft = '4px solid var(--warning)';
      item.style.display = 'flex';
      item.style.flexDirection = 'column';
      item.style.gap = '0.25rem';
      item.style.cursor = 'pointer';
      item.style.textDecoration = 'none';
      item.style.transition = 'all 0.15s ease';
      item.innerHTML = `<div style="display:flex; justify-content:space-between; align-items:center;">
        <strong style="color:var(--warning); font-size:0.8rem;">⚠️ ${escHtml(p.question_num)}</strong>
        <span style="font-size:0.75rem; color:var(--primary);">Перейти →</span>
      </div>
      <span style="color:var(--text); line-height:1.35; font-size:0.82rem;">${escHtml(p.problem_detail)}</span>`;

      item.addEventListener('mouseenter', () => {
        item.style.background = 'var(--bg-card)';
        item.style.borderColor = 'var(--primary)';
      });
      item.addEventListener('mouseleave', () => {
        item.style.background = 'var(--bg-card2)';
        item.style.borderColor = 'var(--border-light)';
        item.style.borderLeftColor = 'var(--warning)';
      });

      item.addEventListener('click', function(e) {
        e.preventDefault();
        const dropdown = document.getElementById('notifications-dropdown');
        if (dropdown) dropdown.style.display = 'none';
        if (!window.highlightQuestion || !window.highlightQuestion(p.target_id)) {
          if (window.showToast) window.showToast('warning', 'Питання було видалено або не знайдено');
        }
      });

      list.appendChild(item);
    });
  } else {
    badge.style.display = 'none';
    list.innerHTML = '<span style="font-size:0.85rem; color:var(--text-muted); text-align:center; padding:0.75rem 0.5rem; display:block;">Проблем не виявлено 🎉</span>';
  }

  if (window.renderProblemsModalContent) {
    window.renderProblemsModalContent();
  }
}

// Global keyboard shortcuts for Ctrl+Z (Undo) and Ctrl+Y (Redo)
window.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z' && !e.shiftKey) {
    // If not inside an active editable input with text selection
    const activeEl = document.activeElement;
    if (activeEl && (activeEl.tagName === 'INPUT' || activeEl.tagName === 'TEXTAREA')) {
      // Allow normal text undo inside inputs, but if at history state trigger undoLastAction
    } else {
      e.preventDefault();
      window.undoLastAction();
    }
  } else if (((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') || ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'z')) {
    e.preventDefault();
    window.redoLastAction();
  }
});

document.addEventListener('DOMContentLoaded', () => {
  if (typeof INITIAL_TEST_DATA !== 'undefined' && INITIAL_TEST_DATA.questions) {
    INITIAL_TEST_DATA.questions.forEach(q => addQuestion(q, false, true));
    
    // Load initial check status of toggle grouping if saved or default
    if (INITIAL_TEST_DATA.excluded_topics) {
      try {
        const excl = JSON.parse(INITIAL_TEST_DATA.excluded_topics);
        const exclInput = document.getElementById('excluded_topics');
        if (exclInput) exclInput.value = JSON.stringify(excl);
      } catch(e) {}
    }
  } else if (document.getElementById('initial-test-data')) {
    try {
      const data = JSON.parse(document.getElementById('initial-test-data').textContent);
      if (data.questions) {
        data.questions.forEach(q => addQuestion(q, false, true));
      }
      if (data.excluded_topics) {
        const exclInput = document.getElementById('excluded_topics');
        if (exclInput) exclInput.value = typeof data.excluded_topics === 'string' ? data.excluded_topics : JSON.stringify(data.excluded_topics);
      }
    } catch (e) {}
  }
  
  if (typeof INITIAL_TEST_DATA === 'undefined' || !INITIAL_TEST_DATA.id) {
    uploadSessionId = 'temp_' + Math.random().toString(36).substring(2, 15) + Math.random().toString(36).substring(2, 15);
    const sessInput = document.getElementById('upload_session_id');
    if (sessInput) {
      sessInput.value = uploadSessionId;
    }
  }
  
  updateEmptyMsg();
  updateCounter();
  renderTopicsManagement();
  updateUndoRedoButtons();
  scheduleValidation(200);

  // Check URL hash for question navigation on load
  checkUrlHashForQuestion();
});

function checkUrlHashForQuestion() {
  if (window.location.hash && window.location.hash.startsWith('#question-')) {
    const targetDbId = window.location.hash.replace('#question-', '');
    if (targetDbId) {
      setTimeout(() => window.highlightQuestion(targetDbId), 150);
    }
  }
}

window.addEventListener('hashchange', checkUrlHashForQuestion);

window.highlightQuestion = function(targetDbId) {
  if (!targetDbId) return false;
  
  let targetQ = questions.find(q => String(q.db_id) === String(targetDbId) || String(q._id) === String(targetDbId));
  let el = null;
  
  if (targetQ) {
    el = document.getElementById(`qblock-${targetQ._id}`);
  }
  
  if (!el) {
    el = document.querySelector(`[data-question-id="${targetDbId}"]`) || 
         document.getElementById(`question-${targetDbId}`) || 
         document.getElementById(`qblock-${targetDbId}`);
  }

  if (el) {
    // Expand the question if it's collapsed
    const body = el.querySelector('.question-editor-body');
    if (body) {
      if (body.style.display === 'none') {
        body.style.display = 'flex';
      }
      body.classList.remove('collapsed');
    }

    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    
    // Smooth pulsating highlight without page flickering
    el.classList.remove('question-attention-pulse');
    void el.offsetWidth; // Trigger reflow for re-animation
    el.classList.add('question-attention-pulse');

    setTimeout(() => {
      el.classList.remove('question-attention-pulse');
    }, 3500);
    return true;
  }
  return false;
};

function addQuestion(data = null, doScroll = true, fromHistory = false) {
  if (!fromHistory) {
    saveHistoryState();
  }
  const local_id = ++questionCounter;
  const q = {
    _id: local_id,
    db_id: data?.id || null,
    question_text: data?.question_text || '',
    question_type: data?.question_type || 'single_choice',
    points: data?.points ?? 1.0,
    topic: data?.topic || '',
    difficulty: data?.difficulty || 'medium',
    explanation: data?.explanation || '',
    order_index: data?.order_index ?? questions.length,
    image_url: data?.image_url || null,
    options: data?.options ? data.options.map(o => ({ ...o, db_id: o.id || null })) : [],
  };
  questions.push(q);

  const container = document.getElementById('questions-container');
  const el = buildQuestionEl(q);
  
  const isGrouped = document.getElementById('toggle-grouping') && document.getElementById('toggle-grouping').checked;
  if (isGrouped) {
    regroupQuestions();
  } else {
    container.appendChild(el);
  }

  updateEmptyMsg();
  updateCounter();
  renderTopicsManagement();
  if (!fromHistory) {
    scheduleValidation(150);
  }
  
  if (q.question_type === 'hotspot' && q.image_url) {
    initHotspotEditor(q._id);
  }
  
  if (doScroll) {
    const targetEl = isGrouped ? document.getElementById(`qblock-${local_id}`) : el;
    if (targetEl) scrollToEl(targetEl);
  }
}

function buildQuestionEl(q) {
  const wrap = document.createElement('div');
  wrap.className = 'question-editor';
  wrap.id = `qblock-${q._id}`;
  if (q.db_id) {
    wrap.dataset.questionId = q.db_id;
    wrap.setAttribute('data-question-id', q.db_id);
  }

  const imageHtml = q.image_url 
    ? `<div style="position:relative;display:inline-block">
         <img src="${q.image_url}" class="test-image-preview" />
         <button type="button" class="btn btn-sm btn-danger" style="position:absolute;top:0;right:5px;padding:0 5px;" onclick="updateQField(${q._id}, 'image_url', null);rebuildQuestionBody(${q._id})">✕</button>
       </div>` 
    : `<button type="button" class="image-upload-btn" onclick="triggerUpload('q-${q._id}')">🖼️ Додати зображення</button>
       <input type="file" id="upload-q-${q._id}" style="display:none" accept="image/*" onchange="handleUpload(this, (url) => { updateQField(${q._id}, 'image_url', url); rebuildQuestionBody(${q._id}); })" />`;

  wrap.innerHTML = `
    <div class="question-editor-header" onclick="toggleQuestion(${q._id})">
      <span class="question-editor-title" id="qtitle-${q._id}">
        ${q.question_text ? escHtml(q.question_text.slice(0, 60)) : 'Нове питання'}
      </span>
      <div style="display:flex;gap:.5rem;align-items:center">
        <button type="button" class="btn btn-sm btn-secondary q-move-btn" onclick="event.stopPropagation();moveQuestion(${q._id},-1)" title="Вгору">↑</button>
        <button type="button" class="btn btn-sm btn-secondary q-move-btn" onclick="event.stopPropagation();moveQuestion(${q._id},1)" title="Вниз">↓</button>
        <button type="button" class="btn btn-sm btn-secondary" onclick="event.stopPropagation();duplicateQuestion(${q._id})" title="Дублювати питання">📋</button>
        <button type="button" class="btn btn-sm btn-danger" onclick="event.stopPropagation();removeQuestion(${q._id})" title="Видалити питання">✕</button>
      </div>
    </div>
    <div class="question-editor-body" id="qbody-${q._id}">
      <div class="form-group">
        <label>Текст питання *</label>
        <textarea rows="2" placeholder="Введіть питання..."
          oninput="updateQField(${q._id},'question_text',this.value)"
        >${escHtml(q.question_text)}</textarea>
        <div style="margin-top:0.5rem">
          ${imageHtml}
        </div>
      </div>

      <div class="form-grid">
        <div class="form-group">
          <label>Тип питання</label>
          <select onchange="updateQField(${q._id},'question_type',this.value);rebuildOptions(${q._id})">
            <option value="single_choice"   ${q.question_type==='single_choice'?'selected':''}>Одна відповідь</option>
            <option value="multiple_choice" ${q.question_type==='multiple_choice'?'selected':''}>Декілька відповідей</option>
            <option value="true_false"      ${q.question_type==='true_false'?'selected':''}>Так / Ні</option>
            <option value="short_text"      ${q.question_type==='short_text'?'selected':''}>Коротка відповідь</option>
            <option value="image_choice"    ${q.question_type==='image_choice'?'selected':''}>Вибір зображення</option>
            <option value="matching"        ${q.question_type==='matching'?'selected':''}>Відповідність</option>
            <option value="sequence"        ${q.question_type==='sequence'?'selected':''}>Послідовність</option>
            <option value="hotspot"         ${q.question_type==='hotspot'?'selected':''}>Вказівка на зображенні</option>
          </select>
        </div>
        <div class="form-group">
          <label>Балів</label>
          <input type="number" min="0.5" step="0.5" value="${q.points}"
            onchange="updateQField(${q._id},'points',parseFloat(this.value)||1)"/>
        </div>
         <div class="form-group">
          <label>Тема (необов'язково)</label>
          <input type="text" value="${escHtml(q.topic||'')}" placeholder="Тема"
            oninput="updateQField(${q._id},'topic',this.value)"
            onblur="onTopicInputBlur()"/>
        </div>
        <div class="form-group">
          <label>Складність</label>
          <select onchange="updateQField(${q._id},'difficulty',this.value)">
            <option value="easy"   ${q.difficulty==='easy'?'selected':''}>Легка</option>
            <option value="medium" ${q.difficulty==='medium'?'selected':''}>Середня</option>
            <option value="hard"   ${q.difficulty==='hard'?'selected':''}>Важка</option>
          </select>
        </div>
        <div class="form-group form-group--full">
          <label>Пояснення (після відповіді)</label>
          <input type="text" value="${escHtml(q.explanation||'')}" placeholder="Пояснення правильної відповіді"
            oninput="updateQField(${q._id},'explanation',this.value)"/>
        </div>
      </div>

      <div id="options-area-${q._id}">
        ${buildOptionsHTML(q)}
      </div>

      <div style="display:flex; justify-content:flex-end; gap:0.5rem; margin-top:0.75rem; padding-top:0.75rem; border-top:1px dashed var(--border);">
        <button type="button" class="btn btn-sm btn-secondary" onclick="duplicateQuestion(${q._id})" title="Дублювати питання">📋 Дублювати питання</button>
        <button type="button" class="btn btn-sm btn-danger" onclick="removeQuestion(${q._id})" title="Видалити питання">✕ Видалити питання</button>
      </div>
    </div>
  `;
  return wrap;
}

function rebuildQuestionBody(id) {
  const q = questions.find(q => q._id === id);
  if (!q) return;
  const container = document.getElementById(`qblock-${id}`);
  if (!container) return;
  const newEl = buildQuestionEl(q);
  container.innerHTML = newEl.innerHTML;
  const body = document.getElementById(`qbody-${id}`);
  if (body) body.style.display = 'flex';
  if (q.question_type === 'hotspot' && q.image_url) {
    initHotspotEditor(id);
  }
}

function triggerUpload(id) {
  document.getElementById(`upload-${id}`).click();
}

async function handleUpload(input, callback) {
  if (!input.files || !input.files[0]) return;
  const formData = new FormData();
  formData.append('file', input.files[0]);

  let uploadUrl = '/teacher/upload-image';
  if (typeof INITIAL_TEST_DATA !== 'undefined' && INITIAL_TEST_DATA.id) {
    uploadUrl += `?test_id=${INITIAL_TEST_DATA.id}`;
  } else if (uploadSessionId) {
    uploadUrl += `?session_id=${uploadSessionId}`;
  }

  try {
    const res = await fetch(uploadUrl, {
      method: 'POST',
      body: formData
    });
    if (!res.ok) throw new Error('Помилка завантаження');
    const data = await res.json();
    callback(data.url);
  } catch (err) {
    alert(err.message);
  }
}

function buildOptionsHTML(q) {
  const type = q.question_type;

  if (type === 'short_text') {
    const val = q.options.length ? escHtml(q.options[0].option_text) : '';
    return `
      <div class="form-group">
        <label>Правильна відповідь</label>
        <input type="text" value="${val}" placeholder="Точна відповідь (регістр не важливий)"
          oninput="setShortAnswer(${q._id},this.value)"/>
        <p class="form-hint">Система порівнює без урахування регістру та зайвих пробілів</p>
      </div>`;
  }

  if (type === 'hotspot') {
    const val = q.options.length ? escHtml(q.options[0].option_text) : '';
    const imagePreviewHtml = q.image_url ? `
      <div class="hotspot-editor-container" id="hotspot-editor-${q._id}" style="position:relative; display:inline-block; max-width:100%; margin-bottom:1rem; border:1px solid var(--border-light); border-radius:6px; overflow:hidden;">
        <img id="hotspot-editor-img-${q._id}" src="${q.image_url}" style="max-width:100%; display:block; user-select:none; -webkit-user-drag:none;" onload="initHotspotEditor(${q._id})" />
        <svg id="hotspot-editor-svg-${q._id}" style="position:absolute; top:0; left:0; width:100%; height:100%; cursor:crosshair;">
          <polygon id="hotspot-poly-${q._id}" fill="rgba(59, 130, 246, 0.3)" stroke="#3b82f6" stroke-width="2" points="" style="display:none" />
          <rect id="hotspot-drag-rect-${q._id}" fill="rgba(59, 130, 246, 0.15)" stroke="#3b82f6" stroke-dasharray="4" stroke-width="1.5" x="0" y="0" width="0" height="0" style="display:none" />
        </svg>
      </div>
    ` : '<p class="text-muted" style="margin-bottom:1rem">Додайте зображення до питання вище, щоб мати можливість виділити область на ньому мишкою.</p>';

    return `
      <div class="form-group">
        <label>Правильна область (координати полігону)</label>
        <div style="margin-top:0.5rem">
          ${imagePreviewHtml}
        </div>
        <input type="text" id="hotspot-input-${q._id}" value="${val}" placeholder="(x1,y1)-(x2,y2)..." class="form-control"
          oninput="setHotspotAnswer(${q._id},this.value); drawSavedHotspot(${q._id});"/>
        <p class="form-hint">Виділіть прямокутник курсором мишки на зображенні вище, або введіть координати у форматі: (x1,y1)-(x2,y2)...</p>
      </div>`;
  }

  if (type === 'true_false') {
    const trueSelected  = q.options.find(o => o.option_text === 'Так'  && o.is_correct);
    const falseSelected = q.options.find(o => o.option_text === 'Ні' && o.is_correct);
    return `
      <div class="form-group">
        <label>Правильна відповідь</label>
        <div style="display:flex;gap:.75rem">
          <label class="checkbox-label">
            <input type="radio" name="tf-${q._id}" value="true" ${trueSelected?'checked':''}
              onchange="setTrueFalse(${q._id},true)"/> Так
          </label>
          <label class="checkbox-label">
            <input type="radio" name="tf-${q._id}" value="false" ${falseSelected?'checked':''}
              onchange="setTrueFalse(${q._id},false)"/> Ні
          </label>
        </div>
      </div>`;
  }

  const isMulti = type === 'multiple_choice';
  const isImage = type === 'image_choice';
  const isMatching = type === 'matching';
  const isSequence = type === 'sequence';

  let html = `
    <div class="form-group">
      <label>${isSequence ? 'Пункти в правильному порядку' : isMatching ? 'Пари (Ліва -> Права)' : 'Варіанти відповідей'}</label>
      <div class="options-list" id="optlist-${q._id}">`;

  q.options.forEach((o, i) => { 
    html += optionRowHTML(q._id, i, o, type); 
  });

  html += `
      </div>
      <button type="button" class="btn btn-sm btn-secondary" style="margin-top:.5rem"
        onclick="addOption(${q._id})">+ Додати ${isMatching ? 'пару' : 'варіант'}</button>
    </div>`;
  return html;
}

function optionRowHTML(qid, idx, o, type) {
  const isMulti = type === 'multiple_choice';
  const isImage = type === 'image_choice';
  const isMatching = type === 'matching';
  const isSequence = type === 'sequence';
  
  if (isMatching) {
    return `
      <div class="matching-row" id="opt-${qid}-${idx}">
        <input type="text" value="${escHtml(o.option_text||'')}" placeholder="Ліва частина"
          oninput="updateOptionField(${qid},${idx},'option_text',this.value)" style="flex:1"/>
        <span class="matching-arrow">→</span>
        <input type="text" value="${escHtml(o.matching_text||'')}" placeholder="Права частина"
          oninput="updateOptionField(${qid},${idx},'matching_text',this.value)" style="flex:1"/>
        <button type="button" class="btn-remove-option" onclick="removeOption(${qid},${idx})" title="Видалити">✕</button>
      </div>`;
  }

  if (isSequence) {
    return `
      <div class="option-row" id="opt-${qid}-${idx}">
        <span class="badge badge-count" style="width:28px;height:28px;min-width:28px">${idx+1}</span>
        <input type="text" value="${escHtml(o.option_text||'')}" placeholder="Пункт послідовності"
          oninput="updateOptionField(${qid},${idx},'option_text',this.value)" style="flex:1"/>
        <button type="button" class="btn btn-sm btn-secondary" onclick="moveOption(${qid},${idx},-1)">↑</button>
        <button type="button" class="btn btn-sm btn-secondary" onclick="moveOption(${qid},${idx},1)">↓</button>
        <button type="button" class="btn-remove-option" onclick="removeOption(${qid},${idx})" title="Видалити">✕</button>
      </div>`;
  }

  const inputType = isMulti ? 'checkbox' : 'radio';
  
  const imgHtml = isImage ? (o.image_url 
    ? `<img src="${o.image_url}" class="test-image-preview" style="height:40px;width:40px;margin-right:0" />
       <button type="button" class="btn btn-sm btn-danger" onclick="updateOptionField(${qid},${idx},'image_url',null);rebuildOptions(${qid})">✕</button>`
    : `<button type="button" class="image-upload-btn" onclick="triggerUpload('o-${qid}-${idx}')">🖼️</button>
       <input type="file" id="upload-o-${qid}-${idx}" style="display:none" accept="image/*" onchange="handleUpload(this, (url) => { updateOptionField(${qid},${idx},'image_url',url); rebuildOptions(${qid}); })" />`
  ) : '';

  return `
    <div class="option-row" id="opt-${qid}-${idx}">
      <input type="${inputType}" name="correct-${qid}" ${o.is_correct ? 'checked' : ''}
        onchange="toggleOptionCorrect(${qid},${idx},this.checked,${isMulti})"/>
      ${imgHtml}
      <input type="text" value="${escHtml(o.option_text||'')}" placeholder="Варіант відповіді"
        oninput="updateOptionField(${qid},${idx},'option_text',this.value)" style="flex:1"/>
      <button type="button" class="btn-remove-option" onclick="removeOption(${qid},${idx})" title="Видалити">✕</button>
    </div>`;
}

// Mutators
function updateQField(id, field, value) {
  const q = questions.find(q => q._id === id);
  if (!q) return;
  if (q[field] !== value) {
    if (textInputHistoryTimer) clearTimeout(textInputHistoryTimer);
    textInputHistoryTimer = setTimeout(() => {
      saveHistoryState();
    }, 400);
    q[field] = value;
    if (field === 'question_text') {
      const title = document.getElementById(`qtitle-${id}`);
      if (title) title.textContent = value.slice(0, 60) || 'Нове питання';
      scheduleValidation(300);
    } else if (field === 'topic') {
      renderTopicsManagement();
    }
  }
}

function rebuildOptions(id) {
  saveHistoryState();
  const q = questions.find(q => q._id === id);
  if (!q) return;
  if (q.question_type === 'true_false') {
    q.options = [
      { option_text: 'Так', is_correct: false, order_index: 0 },
      { option_text: 'Ні',  is_correct: true,  order_index: 1 },
    ];
  } else if (q.question_type === 'short_text') {
    q.options = q.options.length ? [{ ...q.options[0], is_correct: true }] : [];
  } else if (q.question_type === 'matching' || q.question_type === 'sequence') {
    q.options.forEach(o => o.is_correct = true); // Всі пункти є частиною правильної відповіді
  }
  document.getElementById(`options-area-${id}`).innerHTML = buildOptionsHTML(q);
  if (q.question_type === 'hotspot' && q.image_url) {
    initHotspotEditor(id);
  }
  scheduleValidation(150);
}

function addOption(qid) {
  saveHistoryState();
  const q = questions.find(q => q._id === qid);
  if (!q) return;
  const idx = q.options.length;
  q.options.push({ option_text: '', is_correct: (q.question_type==='matching'||q.question_type==='sequence'), order_index: idx });
  document.getElementById(`options-area-${qid}`).innerHTML = buildOptionsHTML(q);
  scheduleValidation(150);
}

function removeOption(qid, idx) {
  saveHistoryState();
  const q = questions.find(q => q._id === qid);
  if (!q) return;
  q.options.splice(idx, 1);
  q.options.forEach((o, i) => o.order_index = i);
  document.getElementById(`options-area-${qid}`).innerHTML = buildOptionsHTML(q);
  scheduleValidation(50);
}

function moveOption(qid, idx, dir) {
  saveHistoryState();
  const q = questions.find(q => q._id === qid);
  if (!q) return;
  const newIdx = idx + dir;
  if (newIdx < 0 || newIdx >= q.options.length) return;
  [q.options[idx], q.options[newIdx]] = [q.options[newIdx], q.options[idx]];
  q.options.forEach((o, i) => o.order_index = i);
  document.getElementById(`options-area-${qid}`).innerHTML = buildOptionsHTML(q);
}

function updateOptionField(qid, idx, field, value) {
  const q = questions.find(q => q._id === qid);
  if (q && q.options[idx] !== undefined) {
    if (q.options[idx][field] !== value) {
      if (textInputHistoryTimer) clearTimeout(textInputHistoryTimer);
      textInputHistoryTimer = setTimeout(() => {
        saveHistoryState();
      }, 400);
      q.options[idx][field] = value;
      scheduleValidation(300);
    }
  }
}

function toggleOptionCorrect(qid, idx, checked, isMulti) {
  saveHistoryState();
  const q = questions.find(q => q._id === qid);
  if (!q) return;
  if (!isMulti) q.options.forEach(o => { o.is_correct = false; });
  if (q.options[idx] !== undefined) q.options[idx].is_correct = checked;
  scheduleValidation(150);
}

function setShortAnswer(qid, value) {
  saveHistoryState();
  const q = questions.find(q => q._id === qid);
  if (!q) return;
  q.options = [{ option_text: value, is_correct: true, order_index: 0 }];
  scheduleValidation(150);
}

function setHotspotAnswer(qid, value) {
  saveHistoryState();
  const q = questions.find(q => q._id === qid);
  if (!q) return;
  q.options = [{ option_text: value, is_correct: true, order_index: 0 }];
  scheduleValidation(150);
}

function setTrueFalse(qid, trueIsCorrect) {
  saveHistoryState();
  const q = questions.find(q => q._id === qid);
  if (!q) return;
  q.options = [
    { option_text: 'Так', is_correct: trueIsCorrect,  order_index: 0 },
    { option_text: 'Ні',  is_correct: !trueIsCorrect, order_index: 1 },
  ];
  scheduleValidation(150);
}

function removeQuestion(id) {
  if (!confirm('Видалити це питання?')) return;
  saveHistoryState();
  questions = questions.filter(q => q._id !== id);
  
  if (document.getElementById('toggle-grouping') && document.getElementById('toggle-grouping').checked) {
    regroupQuestions();
  } else {
    const el = document.getElementById(`qblock-${id}`);
    if (el) el.remove();
  }
  
  updateEmptyMsg();
  updateCounter();
  renderTopicsManagement();
  scheduleValidation(50);
}

function moveQuestion(id, dir) {
  saveHistoryState();
  const idx = questions.findIndex(q => q._id === id);
  const newIdx = idx + dir;
  if (newIdx < 0 || newIdx >= questions.length) return;
  [questions[idx], questions[newIdx]] = [questions[newIdx], questions[idx]];
  const container = document.getElementById('questions-container');
  const children = [...container.querySelectorAll('.question-editor')];
  if (dir === -1 && newIdx >= 0) {
    container.insertBefore(children[idx], children[newIdx]);
  } else {
    container.insertBefore(children[newIdx], children[idx]);
  }
  questions.forEach((q, i) => { q.order_index = i; });
}

function duplicateQuestion(id) {
  saveHistoryState();
  const idx = questions.findIndex(q => q._id === id);
  if (idx === -1) return;

  const origQ = questions[idx];
  const local_id = ++questionCounter;

  const copiedOptions = (origQ.options || []).map((o, optIdx) => ({
    option_text: o.option_text || '',
    is_correct: !!o.is_correct,
    order_index: o.order_index ?? optIdx,
    matching_text: o.matching_text || '',
    image_url: o.image_url || null,
    hotspot_x: o.hotspot_x ?? null,
    hotspot_y: o.hotspot_y ?? null,
    hotspot_radius: o.hotspot_radius ?? null,
    db_id: null
  }));

  const newQ = {
    _id: local_id,
    db_id: null,
    question_text: origQ.question_text || '',
    question_type: origQ.question_type || 'single_choice',
    points: origQ.points ?? 1.0,
    topic: origQ.topic || '',
    difficulty: origQ.difficulty || 'medium',
    explanation: origQ.explanation || '',
    order_index: idx + 1,
    image_url: origQ.image_url || null,
    options: copiedOptions,
  };

  questions.splice(idx + 1, 0, newQ);
  questions.forEach((q, i) => { q.order_index = i; });

  const container = document.getElementById('questions-container');
  const isGrouped = document.getElementById('toggle-grouping') && document.getElementById('toggle-grouping').checked;

  if (isGrouped) {
    regroupQuestions();
  } else {
    const el = buildQuestionEl(newQ);
    const origEl = document.getElementById(`qblock-${id}`);
    if (origEl && origEl.nextSibling) {
      container.insertBefore(el, origEl.nextSibling);
    } else {
      container.appendChild(el);
    }
  }

  updateEmptyMsg();
  updateCounter();
  renderTopicsManagement();
  scheduleValidation(150);

  if (newQ.question_type === 'hotspot' && newQ.image_url) {
    initHotspotEditor(newQ._id);
  }

  const targetEl = document.getElementById(`qblock-${local_id}`);
  if (targetEl) scrollToEl(targetEl);

  if (window.showToast) {
    window.showToast('success', 'Питання успішно продубльовано 📋');
  }
}
window.duplicateQuestion = duplicateQuestion;

function toggleQuestion(id) {
  const body = document.getElementById(`qbody-${id}`);
  if (!body) return;
  body.style.display = body.style.display === 'none' ? 'flex' : 'none';
}

function prepareSubmit() {
  const checkboxes = document.querySelectorAll('.topic-exclude-checkbox');
  const excluded = [];
  checkboxes.forEach(cb => {
    if (cb.checked) excluded.push(cb.value);
  });
  const exclInput = document.getElementById('excluded_topics');
  if (exclInput) {
    exclInput.value = JSON.stringify(excluded);
  }

  const payload = questions.map((q, i) => ({
    id: q.db_id,
    question_text: q.question_text,
    question_type: q.question_type,
    points: q.points,
    topic: q.topic || null,
    difficulty: q.difficulty,
    explanation: q.explanation || null,
    order_index: i,
    image_url: q.image_url || null,
    options: q.options.map((o, j) => ({
      id: o.db_id,
      option_text: o.option_text,
      matching_text: o.matching_text || null,
      image_url: o.image_url || null,
      is_correct: !!o.is_correct,
      order_index: j,
    })),
  }));

  for (const q of payload) {
    if (!q.question_text.trim() && !q.image_url) {
      alert('Заповніть текст або додайте зображення для кожного питання');
      return false;
    }
    if (['single_choice', 'multiple_choice', 'image_choice'].includes(q.question_type)) {
      if (q.options.length === 0) { alert('Бракує варіантів відповіді'); return false; }
      if (!q.options.some(o => o.is_correct)) { alert('Немає правильного варіанту'); return false; }
    }
    if (q.question_type === 'matching' && q.options.length < 2) {
      alert('Для відповідності потрібно хоча б 2 пари'); return false;
    }
    if (q.question_type === 'sequence' && q.options.length < 2) {
      alert('Для послідовності потрібно хоча б 2 пункти'); return false;
    }
  }

  // Double check if any questions exist at all
  if (payload.length === 0) {
    alert('Тест повинен містити хоча б одне питання');
    return false;
  }

  // Warning if all questions/topics are excluded
  const nonExcludedQuestions = payload.filter(q => !excluded.includes(q.topic || ''));
  if (nonExcludedQuestions.length === 0 && payload.length > 0) {
    if (!confirm('Попередження: ви виключили всі теми з тесту! Учні не отримають жодного питання. Продовжити збереження?')) {
      return false;
    }
  }

  document.getElementById('questions_json').value = JSON.stringify(payload);
  return true;
}

function updateEmptyMsg() {
  const msg = document.getElementById('empty-questions-msg');
  if (!msg) return;
  msg.style.display = questions.length === 0 ? 'block' : 'none';
}

function updateCounter() {
  const el = document.getElementById('question-count');
  if (el) el.textContent = questions.length;
}

function scrollToEl(el) {
  setTimeout(() => el.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 50);
}

function escHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function renderTopicsManagement() {
  const container = document.getElementById('topics-management-container');
  if (!container) return;

  const uniqueTopics = [...new Set(questions.map(q => q.topic).filter(t => t && t.trim() !== ''))];
  if (uniqueTopics.length === 0) {
    container.innerHTML = '<p class="text-muted" style="margin:0">У тесті немає розділів/тем. Вкажіть тему в питаннях для їх групування.</p>';
    return;
  }

  let excluded = [];
  try {
    const val = document.getElementById('excluded_topics').value;
    if (val) excluded = JSON.parse(val);
  } catch (e) {
    excluded = [];
  }

  container.innerHTML = `
    <div style="display:grid;grid-template-columns:repeat(auto-fill, minmax(280px, 1fr));gap:1rem">
      ${uniqueTopics.map(topic => {
        const isExcluded = excluded.includes(topic);
        const qCount = questions.filter(q => q.topic === topic).length;
        return `
          <label class="checkbox-label" style="display:flex;align-items:center;gap:0.75rem;background:var(--bg-card2);padding:0.75rem;border-radius:6px;border:1px solid ${isExcluded ? 'var(--danger-glow)' : 'var(--border-light)'};margin:0;cursor:pointer">
            <input type="checkbox" class="topic-exclude-checkbox" value="${escHtml(topic)}" ${isExcluded ? 'checked' : ''} onchange="onTopicExcludeChange()" />
            <div style="flex:1">
              <div style="font-weight:600;color:${isExcluded ? 'var(--text-muted)' : 'var(--text)'}">${escHtml(topic)}</div>
              <small class="text-muted">${qCount} питань</small>
            </div>
            <span class="badge ${isExcluded ? 'badge-danger' : 'badge-success'}" style="font-size:0.75rem">
              ${isExcluded ? 'Виключено' : 'Активно'}
            </span>
          </label>
        `;
      }).join('')}
    </div>
  `;
}

function onTopicExcludeChange() {
  const checkboxes = document.querySelectorAll('.topic-exclude-checkbox');
  const excluded = [];
  checkboxes.forEach(cb => {
    if (cb.checked) excluded.push(cb.value);
  });
  document.getElementById('excluded_topics').value = JSON.stringify(excluded);
  renderTopicsManagement();
}

function onTopicInputBlur() {
  if (document.getElementById('toggle-grouping') && document.getElementById('toggle-grouping').checked) {
    regroupQuestions();
  }
  renderTopicsManagement();
}

function toggleGrouping(enabled) {
  regroupQuestions();
  const moveBtns = document.querySelectorAll('.q-move-btn');
  moveBtns.forEach(btn => {
    btn.style.display = enabled ? 'none' : 'inline-block';
  });
}

function regroupQuestions() {
  const container = document.getElementById('questions-container');
  if (!container) return;

  const isGrouped = document.getElementById('toggle-grouping') && document.getElementById('toggle-grouping').checked;

  if (!isGrouped) {
    // Clear everything and render flat questions
    container.innerHTML = `
      <div class="empty-questions" id="empty-questions-msg" style="display:none">
        <p>Питань ще немає. Натисніть «Додати питання».</p>
      </div>
    `;
    updateEmptyMsg();
    questions.forEach(q => {
      container.appendChild(buildQuestionEl(q));
      if (q.question_type === 'hotspot' && q.image_url) {
        initHotspotEditor(q._id);
      }
    });
    return;
  }

  // Clear container
  container.innerHTML = `
    <div class="empty-questions" id="empty-questions-msg" style="display:none">
      <p>Питань ще немає. Натисніть «Додати питання».</p>
    </div>
  `;
  updateEmptyMsg();

  // Group questions by topic
  const groups = {};
  questions.forEach(q => {
    const t = (q.topic || '').trim() || 'Без розділу';
    if (!groups[t]) groups[t] = [];
    groups[t].push(q);
  });

  // Render group sections
  Object.entries(groups).forEach(([topic, list]) => {
    const sec = document.createElement('div');
    sec.className = 'topic-group-section card mt-2';
    sec.style.borderLeft = '4px solid var(--primary)';
    sec.style.background = 'var(--bg-card)';
    sec.style.boxShadow = 'var(--shadow-sm)';
    sec.style.padding = '1rem';
    
    sec.innerHTML = `
      <div class="card-header-row" style="background:var(--bg-card2);padding:0.75rem 1rem;border-radius:6px;margin-bottom:1rem">
        <h3 style="margin:0;font-size:1.1rem;color:var(--primary)">Розділ: ${escHtml(topic)}</h3>
        <span class="badge badge-count" style="background:var(--primary-glow);color:var(--primary)">${list.length} питань</span>
      </div>
      <div class="topic-questions-list" style="display:flex;flex-direction:column;gap:1rem"></div>
    `;
    
    const listDiv = sec.querySelector('.topic-questions-list');
    list.forEach(q => {
      const qEl = buildQuestionEl(q);
      listDiv.appendChild(qEl);
      if (q.question_type === 'hotspot' && q.image_url) {
        initHotspotEditor(q._id);
      }
    });
    
    container.appendChild(sec);
  });
}

// ---------------------------------------------------------------------------
// Hotspot Editor Helper Functions
// ---------------------------------------------------------------------------
function initHotspotEditor(qid) {
  const img = document.getElementById(`hotspot-editor-img-${qid}`);
  const svg = document.getElementById(`hotspot-editor-svg-${qid}`);
  if (!img || !svg) return;

  if (svg.dataset.initialized) {
    drawSavedHotspot(qid);
    return;
  }
  svg.dataset.initialized = 'true';

  // Draw any saved hotspot area
  drawSavedHotspot(qid);

  // Setup mouse events for drawing rect
  let startX = 0;
  let startY = 0;
  let isDrawing = false;
  const dragRect = document.getElementById(`hotspot-drag-rect-${qid}`);

  svg.addEventListener('mousedown', (e) => {
    e.preventDefault();
    const rect = svg.getBoundingClientRect();
    startX = e.clientX - rect.left;
    startY = e.clientY - rect.top;
    isDrawing = true;

    if (dragRect) {
      dragRect.setAttribute('x', startX);
      dragRect.setAttribute('y', startY);
      dragRect.setAttribute('width', 0);
      dragRect.setAttribute('height', 0);
      dragRect.style.display = 'block';
    }
  });

  svg.addEventListener('mousemove', (e) => {
    if (!isDrawing) return;
    const rect = svg.getBoundingClientRect();
    const currentX = e.clientX - rect.left;
    const currentY = e.clientY - rect.top;

    const x = Math.min(startX, currentX);
    const y = Math.min(startY, currentY);
    const width = Math.abs(startX - currentX);
    const height = Math.abs(startY - currentY);

    if (dragRect) {
      dragRect.setAttribute('x', x);
      dragRect.setAttribute('y', y);
      dragRect.setAttribute('width', width);
      dragRect.setAttribute('height', height);
    }
  });

  const onMouseUp = (e) => {
    if (!isDrawing) return;
    isDrawing = false;
    if (dragRect) {
      dragRect.style.display = 'none';
    }

    const rect = svg.getBoundingClientRect();
    const endX = e.clientX - rect.left;
    const endY = e.clientY - rect.top;

    const x1 = Math.min(startX, endX);
    const y1 = Math.min(startY, endY);
    const x2 = Math.max(startX, endX);
    const y2 = Math.max(startY, endY);

    // Only apply if the selection has some minimum size
    const width = x2 - x1;
    const height = y2 - y1;
    if (width > 5 && height > 5) {
      const scaleX = img.naturalWidth / rect.width;
      const scaleY = img.naturalHeight / rect.height;

      const natX1 = Math.round(x1 * scaleX);
      const natY1 = Math.round(y1 * scaleY);
      const natX2 = Math.round(x2 * scaleX);
      const natY2 = Math.round(y2 * scaleY);

      const val = `(${natX1},${natY1})-(${natX2},${natY1})-(${natX2},${natY2})-(${natX1},${natY2})`;
      
      const input = document.getElementById(`hotspot-input-${qid}`);
      if (input) {
        input.value = val;
      }
      setHotspotAnswer(qid, val);
      drawSavedHotspot(qid);
    }
  };

  svg.addEventListener('mouseup', onMouseUp);
  svg.addEventListener('mouseleave', onMouseUp);
}

function drawSavedHotspot(qid) {
  const img = document.getElementById(`hotspot-editor-img-${qid}`);
  const svg = document.getElementById(`hotspot-editor-svg-${qid}`);
  const poly = document.getElementById(`hotspot-poly-${qid}`);
  if (!img || !svg || !poly) return;

  const input = document.getElementById(`hotspot-input-${qid}`);
  if (!input || !input.value.trim()) {
    poly.style.display = 'none';
    return;
  }

  const val = input.value.trim();
  const parts = val.split('-');
  const points = [];
  
  for (let p of parts) {
    p = p.trim().replace(/^\(|\)$/g, '');
    if (p.includes(',')) {
      const [px, py] = p.split(',');
      const xVal = parseFloat(px);
      const yVal = parseFloat(py);
      if (!isNaN(xVal) && !isNaN(yVal)) {
        points.push({ x: xVal, y: yVal });
      }
    }
  }

  if (points.length < 3) {
    poly.style.display = 'none';
    return;
  }

  const rect = svg.getBoundingClientRect();
  if (rect.width === 0 || rect.height === 0) {
    setTimeout(() => drawSavedHotspot(qid), 100);
    return;
  }

  const scaleX = rect.width / img.naturalWidth;
  const scaleY = rect.height / img.naturalHeight;

  const svgPoints = points.map(p => {
    const x = p.x * scaleX;
    const y = p.y * scaleY;
    return `${x},${y}`;
  }).join(' ');

  poly.setAttribute('points', svgPoints);
  poly.style.display = 'block';
}
