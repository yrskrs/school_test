/* Offer a local copy when a teacher ended the attempt before synchronization. */
(function () {
  const config = JSON.parse(document.getElementById('recovery-config').textContent);
  let cache;
  try { cache = StudentCache.create(config.attempt_id, window.localStorage); }
  catch { return; }
  const confirmed = new Set(config.confirmed_ids);
  const hasValue = value => value != null && (Array.isArray(value) ? value.length > 0
    : typeof value === 'object' ? Object.keys(value).length > 0 : String(value).trim() !== '');
  const unresolved = Object.keys(cache.state.pending).some(id => !confirmed.has(Number(id)))
    || Object.entries(cache.state.answers).some(([id, value]) => !confirmed.has(Number(id)) && hasValue(value));
  if (!unresolved) { cache.clear(); return; }
  document.getElementById('local-recovery').hidden = false;
  document.getElementById('download-local-recovery').addEventListener('click', () => cache.download());
}());
