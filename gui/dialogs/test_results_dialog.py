import webbrowser
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox, QGridLayout
)
from PyQt6.QtCore import Qt

class TestResultsDialog(QDialog):
    def __init__(self, parent, summary_data: dict, base_url: str):
        super().__init__(parent)
        self.summary_data = summary_data
        self.base_url = base_url
        
        self.init_ui()
        
    def init_ui(self):
        title = self.summary_data.get("title", "Тест")
        self.setWindowTitle(f"Результати тесту: {title}")
        self.resize(720, 520)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        
        # 1. Блок базової статистики
        stats_group = QGroupBox("Загальна статистика тесту")
        stats_layout = QGridLayout()
        
        lbl_attempts = QLabel(str(self.summary_data.get("total_attempts", 0)))
        lbl_attempts.setObjectName("lbl_stats_value")
        
        lbl_completed = QLabel(str(self.summary_data.get("completed_attempts", 0)))
        lbl_completed.setObjectName("lbl_stats_value")
        
        avg_score = self.summary_data.get("avg_score", 0.0)
        max_grade = self.summary_data.get("max_grade", 12)
        lbl_avg = QLabel(f"{avg_score} / {max_grade}")
        lbl_avg.setObjectName("lbl_stats_value")
        
        avg_percent = self.summary_data.get("avg_percent", 0.0)
        lbl_percent = QLabel(f"{avg_percent}%")
        lbl_percent.setObjectName("lbl_stats_value")
        
        stats_layout.addWidget(QLabel("Всього спроб:"), 0, 0)
        stats_layout.addWidget(lbl_attempts, 0, 1)
        stats_layout.addWidget(QLabel("Завершених тестів:"), 0, 2)
        stats_layout.addWidget(lbl_completed, 0, 3)
        
        stats_layout.addWidget(QLabel("Середній бал:"), 1, 0)
        stats_layout.addWidget(lbl_avg, 1, 1)
        stats_layout.addWidget(QLabel("Середній %:"), 1, 2)
        stats_layout.addWidget(lbl_percent, 1, 3)
        
        stats_group.setLayout(stats_layout)
        layout.addWidget(stats_group)
        
        # 2. Таблиця учнів
        table_label = QLabel("Список спроб учнів:")
        font = table_label.font()
        font.setBold(True)
        table_label.setFont(font)
        layout.addWidget(table_label)
        
        table = QTableWidget(0, 6)
        table.setHorizontalHeaderLabels([
            "Учень", "Код сесії", "Початок", "Статус", "Бал / Макс", "Відсоток"
        ])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        
        attempts = self.summary_data.get("attempts", [])
        for row, a in enumerate(attempts):
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(a["student_name"]))
            table.setItem(row, 1, QTableWidgetItem(a["access_code"]))
            table.setItem(row, 2, QTableWidgetItem(a["started_at"]))
            table.setItem(row, 3, QTableWidgetItem(a["status"]))
            
            score_str = f"{a['score']} / {a['max_score']}" if a['raw_status'] in ('finished', 'timeout', 'stopped') else "—"
            table.setItem(row, 4, QTableWidgetItem(score_str))
            
            pct_str = f"{a['percent']}%" if a['raw_status'] in ('finished', 'timeout', 'stopped') else "—"
            table.setItem(row, 5, QTableWidgetItem(pct_str))
            
        layout.addWidget(table, stretch=1)
        
        # 3. Нижні кнопки
        bottom_layout = QHBoxLayout()
        btn_browser = QPushButton("🌐 Відкрити деталі у браузері")
        btn_browser.clicked.connect(self.open_in_browser)
        
        btn_close = QPushButton("Закрити")
        btn_close.clicked.connect(self.accept)
        
        bottom_layout.addWidget(btn_browser)
        bottom_layout.addStretch()
        bottom_layout.addWidget(btn_close)
        
        layout.addLayout(bottom_layout)
        
    def open_in_browser(self):
        webbrowser.open(f"{self.base_url}/teacher/results")
