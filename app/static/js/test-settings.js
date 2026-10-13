(function () {
  const mode = document.getElementById('attempt_limit_mode');
  const limit = document.getElementById('max_attempts');
  if (!mode || !limit) return;
  function updateAttemptLimit() {
    limit.disabled = mode.value === 'unlimited';
    limit.required = !limit.disabled;
  }
  mode.addEventListener('change', updateAttemptLimit);
  updateAttemptLimit();
})();
