/* Navigation and accessible scrolling shared by every authenticated teacher page. */
(function () {
  'use strict';
  const header = document.querySelector('header.navbar');
  const nav = document.getElementById('teacher-navigation');
  const toggle = document.getElementById('teacher-menu-toggle');
  if (!header || !nav || !toggle) return;

  function updateHeaderHeight() {
    const scale = Number(getComputedStyle(document.documentElement).zoom) || 1;
    document.body.style.setProperty('--teacher-header-height', `${header.getBoundingClientRect().height / scale}px`);
  }
  new ResizeObserver(updateHeaderHeight).observe(header);
  updateHeaderHeight();
  toggle.addEventListener('click', () => {
    const open = nav.classList.toggle('is-open');
    toggle.setAttribute('aria-expanded', String(open));
  });

  document.addEventListener('toggle', event => {
    const menu = event.target;
    if (!menu.matches('.list-menu') || !menu.open) return;
    const panel = menu.querySelector('.list-menu-panel');
    if (!panel) return;
    panel.style.transform = '';
    const bounds = panel.getBoundingClientRect();
    const scale = Number(getComputedStyle(document.documentElement).zoom) || 1;
    const shift = bounds.left < 8 ? 8 - bounds.left : Math.min(0, innerWidth - 8 - bounds.right);
    if (shift) panel.style.transform = `translateX(${shift / scale}px)`;
  }, true);

  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    if (nav.classList.contains('is-open')) {
      nav.classList.remove('is-open');
      toggle.setAttribute('aria-expanded', 'false');
      toggle.focus();
    }
    for (const [buttonId, panelId] of [['navbar-teacher-toggle', 'navbar-teacher-dropdown'], ['notifications-toggle', 'notifications-dropdown']]) {
      const panel = document.getElementById(panelId);
      if (panel && panel.style.display === 'block') {
        panel.style.display = 'none';
        document.getElementById(buttonId)?.setAttribute('aria-expanded', 'false');
        document.getElementById(buttonId)?.focus();
      }
    }
    document.querySelectorAll('.list-menu[open]').forEach(menu => {
      menu.open = false;
      menu.querySelector('summary')?.focus();
    });
  });

  function updateTableHint(table) {
    const overflowing = table.scrollWidth > table.clientWidth + 1;
    let hint = table.previousElementSibling;
    if (!hint?.classList.contains('table-scroll-hint')) {
      hint = document.createElement('p');
      hint.className = 'table-scroll-hint';
      hint.textContent = '↔ Прокрутіть таблицю, щоб побачити всі стовпці';
      table.before(hint);
    }
    hint.hidden = !overflowing;
    if (overflowing) {
      table.tabIndex = 0;
      table.setAttribute('role', 'region');
      table.setAttribute('aria-label', 'Таблиця з горизонтальним прокручуванням');
    } else {
      table.removeAttribute('tabindex');
      table.removeAttribute('role');
      table.removeAttribute('aria-label');
    }
  }
  document.querySelectorAll('.results-table').forEach(table => {
    const labels = Array.from(table.tHead.rows[0].cells, cell => cell.textContent.trim());
    for (const row of table.tBodies[0].rows) {
      if (row.cells.length !== labels.length) continue;
      Array.from(row.cells).forEach((cell, index) => {cell.dataset.label = labels[index];});
    }
  });
  const tableObserver = new ResizeObserver(entries => entries.forEach(entry => updateTableHint(entry.target)));
  document.querySelectorAll('.table-wrapper, .table-responsive, .journal-wrapper, .roster-student-table-wrap').forEach(table => {
    tableObserver.observe(table);
    updateTableHint(table);
  });
})();
