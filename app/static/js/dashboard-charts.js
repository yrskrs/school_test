// Activity Chart
  try {
    const activityDataText = document.getElementById('chart-activity-data')?.textContent;
    const activityLabelsText = document.getElementById('chart-activity-labels')?.textContent;
    
    if (activityDataText && activityLabelsText) {
      const activityData = JSON.parse(activityDataText);
      const activityLabels = JSON.parse(activityLabelsText);

      if (activityData && activityData.length > 0) {
        const activityCtx = document.getElementById('activityChart').getContext('2d');
        new Chart(activityCtx, {
          type: 'line',
          data: {
            labels: activityLabels,
            datasets: [{
              label: 'Завершених тестувань',
              data: activityData,
              borderColor: '#4361ee',
              backgroundColor: 'rgba(67, 97, 238, 0.1)',
              borderWidth: 2,
              fill: true,
              tension: 0.3
            }]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } }
          }
        });
      }
    }
  } catch(e) {
    console.error("Помилка завантаження графіка активності", e);
  }

  // Scores Chart
  try {
    const scoresDataText = document.getElementById('chart-tests-data')?.textContent;
    const scoresLabelsText = document.getElementById('chart-tests-labels')?.textContent;
    
    if (scoresDataText && scoresLabelsText) {
      const scoresData = JSON.parse(scoresDataText);
      const scoresLabels = JSON.parse(scoresLabelsText);

      if (scoresData && scoresData.length > 0) {
        const scoresCtx = document.getElementById('scoresChart').getContext('2d');
        new Chart(scoresCtx, {
          type: 'bar',
          data: {
            labels: scoresLabels,
            datasets: [{
              label: 'Середній бал (з 12)',
              data: scoresData,
              backgroundColor: '#4cc9f0',
              borderRadius: 4
            }]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: true, max: 12 } }
          }
        });
      }
    }
  } catch(e) {
    console.error("Помилка завантаження графіка успішності", e);
  }

  // Classes Chart
  try {
    const classesDataText = document.getElementById('chart-classes-data')?.textContent;
    const classesLabelsText = document.getElementById('chart-classes-labels')?.textContent;
    
    if (classesDataText && classesLabelsText) {
      const classesData = JSON.parse(classesDataText);
      const classesLabels = JSON.parse(classesLabelsText);

      if (classesData && classesData.length > 0) {
        const classesCtx = document.getElementById('classesChart').getContext('2d');
        new Chart(classesCtx, {
          type: 'bar',
          data: {
            labels: classesLabels,
            datasets: [{
              label: 'Середній бал по класах',
              data: classesData,
              backgroundColor: '#f72585',
              borderRadius: 4
            }]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: true, max: 12 } }
          }
        });
      }
    }
  } catch(e) {
    console.error("Помилка завантаження графіка класів", e);
  }
