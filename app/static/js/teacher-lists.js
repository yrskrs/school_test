document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('form[data-confirm]').forEach(form => {
    form.addEventListener('submit', event => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });
  document.querySelectorAll('[data-copy-code]').forEach(button => {
    button.addEventListener('click', async () => {
      const link = `${location.origin}/student/login?code=${encodeURIComponent(button.dataset.copyCode)}`;
      try {
        await navigator.clipboard.writeText(link);
        if (window.showToast) window.showToast('success', 'Посилання для учнів скопійовано');
      } catch {
        window.prompt('Скопіюйте посилання для учнів:', link);
      }
    });
  });
  document.querySelectorAll('[data-session-action]').forEach(button => {
    button.addEventListener('click', async () => {
      if (button.dataset.sessionAction === 'stop-all' && !window.confirm('Завершити проходження для всіх учнів цієї сесії?')) return;
      button.disabled = true;
      try {
        const response = await fetch(`/teacher/sessions/${button.dataset.sessionId}/${button.dataset.sessionAction}`, {method: 'POST', headers: {'Accept': 'application/json'}});
        if (!response.ok) throw new Error('Дію не виконано. Оновіть сторінку та спробуйте знову.');
        const result = await response.json();
        const count = result.paused_count ?? result.resumed_count ?? result.stopped_count ?? 0;
        if (window.showToast) window.showToast('success', `Дію виконано для ${count} учнів`);
        button.closest('details').open = false;
      } catch (error) {
        if (window.showToast) window.showToast('error', error.message);
      } finally { button.disabled = false; }
    });
  });
  document.querySelectorAll('[data-preserve-list]').forEach(form => {
    form.addEventListener('submit', async event => {
      if (event.defaultPrevented) return;
      event.preventDefault();
      const button = form.querySelector('button');
      button.disabled = true;
      try {
        const response = await fetch(form.action, {method: 'POST', body: new FormData(form)});
        if (!response.ok) throw new Error('Не вдалося виконати дію');
        location.reload();
      } catch (error) {
        if (window.showToast) window.showToast('error', error.message);
        button.disabled = false;
      }
    });
  });
  document.addEventListener('click', event => {
    document.querySelectorAll('.list-menu[open]').forEach(menu => {
      if (!menu.contains(event.target)) menu.open = false;
    });
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') document.querySelectorAll('.list-menu[open]').forEach(menu => { menu.open = false; menu.querySelector('summary').focus(); });
  });
});
