/* Local display preference; no server requests, timers, or fullscreen changes. */
(function () {
  'use strict';
  const key = 'schooltest:page-scale';
  const minScale = 80;
  const maxScale = 120;
  const clamp = value => Math.max(minScale, Math.min(maxScale, value));
  let scale = 100;
  try {
    const saved = Number(localStorage.getItem(key));
    if (Number.isFinite(saved) && saved > 0) scale = clamp(Math.round(saved / 10) * 10);
  } catch (e) {}

  function applyScale() {
    const root = document.documentElement;
    root.style.zoom = String(scale / 100);
    root.style.setProperty('--page-scale', String(scale / 100));
    const value = document.getElementById('page-scale-value');
    if (!value) return;
    value.textContent = scale + '%';
    document.querySelector('[data-scale-action="decrease"]').disabled = scale <= minScale;
    document.querySelector('[data-scale-action="increase"]').disabled = scale >= maxScale;
  }

  applyScale();
  document.addEventListener('DOMContentLoaded', () => {
    const controls = document.getElementById('page-scale-controls');
    const toolbar = document.querySelector('.test-topbar-right') || document.querySelector('.teacher-toolbar') || document.querySelector('header.navbar');
    if (toolbar) toolbar.appendChild(controls);
    else {
      const theme = document.getElementById('floating-theme-btn');
      if (theme) document.getElementById('display-tools').appendChild(theme);
    }
    applyScale();
    controls.addEventListener('click', event => {
      const button = event.target.closest('[data-scale-action]');
      if (!button) return;
      const action = button.dataset.scaleAction;
      scale = action === 'reset' ? 100 : clamp(scale + (action === 'increase' ? 10 : -10));
      applyScale();
      try { localStorage.setItem(key, String(scale)); } catch (e) {}
    });
  });
})();
