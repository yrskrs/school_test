async function importTestFile(event, format, title) {
  const input = event.target;
  const file = input.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  input.disabled = true;
  showImportOverlay(title);
  try {
    const response = await fetch(`/teacher/tests/import/${format}`, {
      method: 'POST', body: formData
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Невідома помилка');
    window.location.href = `/teacher/tests/${result.test_id}/edit?imported=1&folder_path=${encodeURIComponent(result.folder_path)}`;
  } catch (error) {
    hideImportOverlay();
    alert('Помилка імпорту: ' + error.message);
  } finally {
    input.disabled = false;
    input.value = '';
  }
}

function handleXmlImport(event) {
  return importTestFile(event, 'xml', 'Імпортування XML-тесту');
}

function handleDocxImport(event) {
  return importTestFile(event, 'docx', 'Імпортування Word-тесту');
}

function handleMtfImport(event) {
  return importTestFile(event, 'mtf', 'Перетворення та імпортування MTF-тесту');
}
