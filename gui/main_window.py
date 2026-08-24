import os
import sys
import webbrowser
from datetime import datetime
from PyQt6.QtWidgets import (
    QMenu,
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, 
    QGroupBox, QLabel, QPushButton, QComboBox, QSpinBox, QFrame, 
    QTableWidget, QTableWidgetItem, QTextEdit, QHeaderView,
    QCheckBox, QApplication, QMessageBox, QTabWidget
)
from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QIcon, QFont, QClipboard, QAction

from .server_manager import ServerManager
from .network_manager import NetworkManager
from .session_manager import SessionManager
from .settings_manager import SettingsManager
from .tray_manager import TrayManager
from .api_client import ApiClient
from .dialogs.test_results_dialog import TestResultsDialog


class ProgressWidget(QWidget):
    def __init__(self, total_questions, answers_list):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 2, 5, 2)
        layout.setSpacing(4)
        
        # Обмежуємо кількість квадратиків
        total_questions = max(1, total_questions)
        answers_list = answers_list[:total_questions]
        
        # Доповнюємо пустими, якщо не вистачає
        answers_list += [None] * (total_questions - len(answers_list))
        
        for ans in answers_list:
            box = QFrame()
            box.setFixedSize(14, 14)
            if ans is True:
                box.setProperty("status", "correct")
            elif ans is False:
                box.setProperty("status", "incorrect")
            else:
                # Сірий (без відповіді або очікує перевірки)
                box.setProperty("status", "none")
            layout.addWidget(box)
            
        layout.addStretch()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        
        self.settings = SettingsManager()
        self.session_manager = SessionManager()
        self.server_manager = None
        self.tray_manager = TrayManager(self, QApplication.instance())
        
        self.init_ui()
        self.init_timers()
        self.load_settings()
        
        # Автозапуск якщо увімкнено
        if self.settings.get_auto_start():
            QTimer.singleShot(500, self.start_server)
            
    def init_ui(self):
        self.setWindowTitle("ШколярТест — Менеджер сервера")
        self.resize(900, 700)
        
        # Центральний віджет
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)
        
        # Верхня панель (Стан, Мережа, Керування)
        top_layout = QHBoxLayout()
        top_layout.setSpacing(15)
        
        # 1. Блок "Стан сервера"
        status_group = QGroupBox("Стан сервера")
        status_layout = QGridLayout()
        
        self.lbl_status = QLabel("Зупинений")
        self.lbl_status.setObjectName("lbl_status_stopped")
        self.lbl_uptime = QLabel("00:00:00")
        self.lbl_cpu = QLabel("0.0%")
        self.lbl_ram = QLabel("0.0 MB")
        self.lbl_version = QLabel("1.0.0")
        
        status_layout.addWidget(QLabel("Статус:"), 0, 0)
        status_layout.addWidget(self.lbl_status, 0, 1)
        status_layout.addWidget(QLabel("Час роботи:"), 1, 0)
        status_layout.addWidget(self.lbl_uptime, 1, 1)
        status_layout.addWidget(QLabel("CPU:"), 2, 0)
        status_layout.addWidget(self.lbl_cpu, 2, 1)
        status_layout.addWidget(QLabel("RAM:"), 3, 0)
        status_layout.addWidget(self.lbl_ram, 3, 1)
        status_layout.addWidget(QLabel("Версія:"), 4, 0)
        status_layout.addWidget(self.lbl_version, 4, 1)
        status_group.setLayout(status_layout)
        top_layout.addWidget(status_group)

        # Статистика
        stats_group = QGroupBox("Статистика")
        stats_layout = QGridLayout()
        
        self.lbl_stat_users = QLabel("0")
        self.lbl_stat_users.setObjectName("lbl_stats_value")
        self.lbl_stat_active = QLabel("0")
        self.lbl_stat_active.setObjectName("lbl_stats_value")
        self.lbl_stat_completed = QLabel("0")
        self.lbl_stat_completed.setObjectName("lbl_stats_value")
        self.lbl_stat_online = QLabel("0")
        self.lbl_stat_online.setObjectName("lbl_stats_value")
        
        self.combo_teacher = QComboBox()
        self.combo_teacher.addItem("Всі вчителі", None)
        self.combo_teacher.currentIndexChanged.connect(self.on_teacher_changed)
        
        self.chk_live_progress = QCheckBox("Онлайн-прогрес")
        self.chk_live_progress.setChecked(True)
        self.chk_live_progress.toggled.connect(self.refresh_statistics)
        
        stats_layout.addWidget(QLabel("Вчитель:"), 0, 0)
        stats_layout.addWidget(self.combo_teacher, 0, 1)
        stats_layout.addWidget(self.chk_live_progress, 1, 0, 1, 2)
        
        stats_layout.addWidget(QLabel("Користувачі:"), 2, 0)
        stats_layout.addWidget(self.lbl_stat_users, 2, 1)
        stats_layout.addWidget(QLabel("Активні тести:"), 3, 0)
        stats_layout.addWidget(self.lbl_stat_active, 3, 1)
        stats_layout.addWidget(QLabel("Завершені:"), 4, 0)
        stats_layout.addWidget(self.lbl_stat_completed, 4, 1)
        stats_layout.addWidget(QLabel("Онлайн:"), 5, 0)
        stats_layout.addWidget(self.lbl_stat_online, 5, 1)
        
        stats_group.setLayout(stats_layout)
        top_layout.addWidget(stats_group)
        
        # 2. Блок "Мережа"
        network_group = QGroupBox("Мережа")
        network_layout = QGridLayout()
        
        self.combo_ip = QComboBox()
        
        self.spin_port = QSpinBox()
        self.spin_port.setRange(1024, 65535)
        self.spin_port.setValue(self.settings.get_port())
        
        self.lbl_full_address = QLabel("http://localhost:8000")
        self.lbl_full_address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        
        self.refresh_ips()
        
        btn_copy = QPushButton("Копіювати")
        btn_copy.clicked.connect(self.copy_address)
        
        btn_refresh_net = QPushButton("Оновити IP")
        btn_refresh_net.clicked.connect(self.refresh_ips)
        
        network_layout.addWidget(QLabel("IP Адреса:"), 0, 0)
        network_layout.addWidget(self.combo_ip, 0, 1)
        network_layout.addWidget(QLabel("Порт:"), 1, 0)
        network_layout.addWidget(self.spin_port, 1, 1)
        network_layout.addWidget(QLabel("Адреса:"), 2, 0)
        network_layout.addWidget(self.lbl_full_address, 2, 1)
        
        net_btns_layout = QHBoxLayout()
        net_btns_layout.addWidget(btn_copy)
        net_btns_layout.addWidget(btn_refresh_net)
        network_layout.addLayout(net_btns_layout, 3, 0, 1, 2)
        
        network_group.setLayout(network_layout)
        top_layout.addWidget(network_group)
        
        # 3. Блок "Керування сервером"
        control_group = QGroupBox("Керування")
        control_layout = QVBoxLayout()
        
        self.btn_start = QPushButton("Запустити сервер")
        self.btn_start.setObjectName("btn_start")
        self.btn_start.clicked.connect(self.start_server)
        
        self.btn_stop = QPushButton("Зупинити сервер")
        self.btn_stop.setObjectName("btn_stop")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_server)
        
        self.btn_restart = QPushButton("Перезапустити")
        self.btn_restart.setEnabled(False)
        self.btn_restart.clicked.connect(self.restart_server)
        
        self.btn_browser = QPushButton("Відкрити сайт")
        self.btn_browser.clicked.connect(self.open_browser)
        
        self.btn_register = QPushButton("👥 Користувачі / Реєстрація")
        self.btn_register.setObjectName("btn_register")
        self.btn_register.clicked.connect(self.open_register_window)
        
        self.chk_autostart = QCheckBox("Автозапуск програми")
        self.chk_autostart.setChecked(self.settings.get_auto_start())
        self.chk_autostart.toggled.connect(self.settings.set_auto_start)
        
        control_layout.addWidget(self.btn_start)
        control_layout.addWidget(self.btn_stop)
        control_layout.addWidget(self.btn_restart)
        control_layout.addWidget(self.btn_browser)
        control_layout.addWidget(self.btn_register)
        control_layout.addWidget(self.chk_autostart)

        self.combo_theme = QComboBox()
        self.combo_theme.addItems(["Темна", "Світла"])
        self.combo_theme.setCurrentText(self.settings.get_theme())
        self.combo_theme.currentTextChanged.connect(self.change_theme)
        
        control_layout.addWidget(self.combo_theme)

        
        control_group.setLayout(control_layout)
        top_layout.addWidget(control_group)
        
        main_layout.addLayout(top_layout)
        

        
        # Головний віджет вкладок (Сесії та Тести)
        self.tab_widget = QTabWidget()
        
        # --- Вкладка 1: "⚡ Активні сесії" ---
        tab_sessions = QWidget()
        tab_sessions_layout = QVBoxLayout(tab_sessions)
        tab_sessions_layout.setContentsMargins(5, 5, 5, 5)
        
        # Таблиця сесій
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "Користувач", "IP", "Тест", "Початок", "Статус", "Прогрес", "Остання активність"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        
        # Масові дії
        mass_actions_layout = QHBoxLayout()
        self.btn_pause_toggle = QPushButton("⏸ Пауза всім")
        self.btn_stop_all = QPushButton("⏹ Зупинити всім")
        
        self.btn_pause_toggle.setEnabled(False)
        self.btn_stop_all.setEnabled(False)
        
        self.btn_pause_toggle.setProperty("action", "pause")
        self.btn_pause_toggle.clicked.connect(self.toggle_pause_all)
        self.btn_stop_all.clicked.connect(lambda: self.mass_action('stop'))
        
        mass_actions_layout.addWidget(self.btn_pause_toggle)
        mass_actions_layout.addWidget(self.btn_stop_all)
        mass_actions_layout.addStretch()
        tab_sessions_layout.addLayout(mass_actions_layout)
        tab_sessions_layout.addWidget(self.table)
        
        self.tab_widget.addTab(tab_sessions, "⚡ Активні сесії")
        
        # --- Вкладка 2: "📚 Каталог тестів" ---
        tab_tests = QWidget()
        tab_tests_layout = QVBoxLayout(tab_tests)
        tab_tests_layout.setContentsMargins(5, 5, 5, 5)
        
        # Кнопки дій для тестів
        tests_actions_layout = QHBoxLayout()
        
        btn_create_test = QPushButton("➕ Створити новий тест")
        btn_create_test.clicked.connect(self.open_create_test_browser)
        
        btn_launch_test = QPushButton("▶ Запустити сесію")
        btn_launch_test.clicked.connect(self.launch_test_direct)
        
        btn_edit_test = QPushButton("✏ Редагувати в браузері")
        btn_edit_test.clicked.connect(self.open_edit_test_browser)
        
        btn_results_test = QPushButton("📊 Результати")
        btn_results_test.clicked.connect(self.show_test_results_dialog)
        
        btn_refresh_tests = QPushButton("🔄 Оновити")
        btn_refresh_tests.clicked.connect(self.refresh_tests)
        
        tests_actions_layout.addWidget(btn_create_test)
        tests_actions_layout.addWidget(btn_launch_test)
        tests_actions_layout.addWidget(btn_edit_test)
        tests_actions_layout.addWidget(btn_results_test)
        tests_actions_layout.addWidget(btn_refresh_tests)
        tests_actions_layout.addStretch()
        tab_tests_layout.addLayout(tests_actions_layout)
        
        # Таблиця тестів
        self.tests_table = QTableWidget(0, 7)
        self.tests_table.setHorizontalHeaderLabels([
            "ID", "Назва тесту", "Предмет", "Клас", "Питань", "Вчитель", "Сесії"
        ])
        self.tests_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tests_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tests_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tests_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tests_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tests_table.customContextMenuRequested.connect(self.show_tests_context_menu)
        self.tests_table.itemDoubleClicked.connect(self.on_test_double_clicked)
        
        tab_tests_layout.addWidget(self.tests_table)
        self.tab_widget.addTab(tab_tests, "📚 Каталог тестів")
        
        main_layout.addWidget(self.tab_widget, stretch=1)
        
        # Журнал подій (Логи)
        log_group = QGroupBox("Журнал подій")
        log_layout = QVBoxLayout()
        
        self.text_log = QTextEdit()
        self.text_log.setReadOnly(True)
        self.text_log.setFixedHeight(120)
        
        log_layout.addWidget(self.text_log)
        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)
        
        # Сигнали віджетів мережі
        self.combo_ip.currentTextChanged.connect(self.update_full_address)
        self.spin_port.valueChanged.connect(self.update_full_address)

    def init_timers(self):
        # Таймер оновлення статистики (1 раз на 5 секунд)
        self.stats_timer = QTimer(self)
        self.stats_timer.timeout.connect(self.refresh_statistics)
        self.stats_timer.start(5000)
        
        # Завантажуємо список вчителів (з затримкою для ініціалізації)
        QTimer.singleShot(1000, self.load_teachers)
        
        # Таймер оновлення даних сервера (1 раз на секунду)
        self.server_monitor_timer = QTimer(self)
        self.server_monitor_timer.timeout.connect(self.update_server_stats)
        self.server_monitor_timer.start(1000)
        
        self.start_time = None

    def refresh_ips(self):
        current = self.combo_ip.currentText()
        self.combo_ip.clear()
        ips = NetworkManager.get_local_ips()
        self.combo_ip.addItems(ips)
        
        # Відновлюємо попередній IP або збережений
        last_ip = current or self.settings.get_last_ip()
        if last_ip in ips:
            self.combo_ip.setCurrentText(last_ip)
            
        self.update_full_address()

    def update_full_address(self):
        ip = self.combo_ip.currentText()
        port = self.spin_port.value()
        self.lbl_full_address.setText(f"http://{ip}:{port}")
        
    def copy_address(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self.lbl_full_address.text())
        self.log_event("Адресу скопійовано в буфер обміну.")

    def log_event(self, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.text_log.append(f"[{timestamp}] {message}")
        
        # Прокрутка до кінця
        scrollbar = self.text_log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        
        # Також пишемо у файл gui.log
        try:
            log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, "gui.log"), "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
        except:
            pass

    @pyqtSlot()
    def start_server(self):
        if self.server_manager and self.server_manager.is_running:
            return
            
        ip = self.combo_ip.currentText()
        port = self.spin_port.value()
        
        # Зберігаємо налаштування
        self.settings.set_port(port)
        self.settings.set_last_ip(ip)
        
        self.server_manager = ServerManager(ip, port)
        self.server_manager.server_output.connect(self.log_event)
        self.server_manager.server_status_changed.connect(self.on_server_status_changed)
        
        self.start_time = datetime.now()
        self.server_manager.start_server(ip, port)
        
        self.combo_ip.setEnabled(False)
        self.spin_port.setEnabled(False)

    @pyqtSlot()
    def stop_server(self):
        if self.server_manager:
            self.server_manager.stop_server()

    @pyqtSlot()
    def restart_server(self):
        self.stop_server()
        QTimer.singleShot(1500, self.start_server)

    @pyqtSlot(bool)
    def on_server_status_changed(self, is_running: bool):
        self.btn_start.setEnabled(not is_running)
        self.btn_stop.setEnabled(is_running)
        self.btn_restart.setEnabled(is_running)
        
        if not is_running:
            self.combo_ip.setEnabled(True)
            self.spin_port.setEnabled(True)
            self.lbl_status.setText("Зупинений")
            self.lbl_status.setObjectName("lbl_status_stopped")
            self.start_time = None
            self.lbl_uptime.setText("00:00:00")
            self.lbl_cpu.setText("0.0%")
            self.lbl_ram.setText("0.0 MB")
        else:
            self.lbl_status.setText("Запущений")
            self.lbl_status.setObjectName("lbl_status_running")
            self.load_teachers()
            
        # Оновлення стилів (оскільки objectName змінився)
        self.lbl_status.style().unpolish(self.lbl_status)
        self.lbl_status.style().polish(self.lbl_status)
        
        self.tray_manager.update_server_status(is_running)

    def open_browser(self):
        webbrowser.open(self.lbl_full_address.text() + "/teacher/login")

    def update_server_stats(self):
        if self.server_manager and self.server_manager.is_running:
            # Uptime
            if self.start_time:
                diff = datetime.now() - self.start_time
                hours, remainder = divmod(diff.seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                self.lbl_uptime.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
                
            # CPU / RAM
            stats = self.server_manager.get_stats()
            self.lbl_cpu.setText(f"{stats['cpu']}%")
            self.lbl_ram.setText(f"{stats['ram_mb']} MB")

    def load_teachers(self):
        """Завантажує список вчителів у випадаючий список."""
        if not self.server_manager or not self.server_manager.is_running:
            return
            
        current_data = self.combo_teacher.currentData()
        if current_data is None and getattr(self, '_first_load_teacher', True):
            last_t = self.settings.get_last_teacher_id()
            current_data = last_t if last_t != 0 else None
            self._first_load_teacher = False
        teachers = self.session_manager.get_teachers()
        
        # Тимчасово відключаємо сигнал щоб не викликати refresh_statistics двічі
        self.combo_teacher.blockSignals(True)
        self.combo_teacher.clear()
        self.combo_teacher.addItem("Всі вчителі", None)
        
        for t in teachers:
            self.combo_teacher.addItem(t["full_name"], t["id"])
            
        # Відновлюємо попередній вибір
        index = self.combo_teacher.findData(current_data)
        if index >= 0:
            self.combo_teacher.setCurrentIndex(index)
            
        self.combo_teacher.blockSignals(False)

    def refresh_statistics(self):
        """Оновлює статистику та таблицю сесій з бази даних."""
        if not self.server_manager or not self.server_manager.is_running:
            return
            
        teacher_id = self.combo_teacher.currentData()
            
        # Загальна статистика
        stats = self.session_manager.get_statistics(teacher_id)
        self.lbl_stat_users.setText(str(stats["active_users"]))
        self.lbl_stat_active.setText(str(stats["active_tests"]))
        self.lbl_stat_completed.setText(str(stats["completed_tests"]))
        self.lbl_stat_online.setText(str(stats["total_online"]))
        
        # Таблиця сесій
        fetch_answers = self.chk_live_progress.isChecked()
        sessions = self.session_manager.get_active_sessions(teacher_id, fetch_answers)
        self.table.setRowCount(0)
        
        in_progress_count = sum(1 for s in sessions if s.get('raw_status') == 'in_progress')
        paused_count = sum(1 for s in sessions if s.get('raw_status') == 'paused')
        
        if in_progress_count > 0:
            self.btn_pause_toggle.setText("⏸ Пауза всім")
            self.btn_pause_toggle.setProperty("action", "pause")
        elif paused_count > 0:
            self.btn_pause_toggle.setText("▶ Відновити всім")
            self.btn_pause_toggle.setProperty("action", "resume")
        else:
            self.btn_pause_toggle.setText("⏸ Пауза всім")
            self.btn_pause_toggle.setProperty("action", "pause")
            
        has_sessions = len(sessions) > 0
        self.btn_pause_toggle.setEnabled(has_sessions and (in_progress_count > 0 or paused_count > 0))
        self.btn_stop_all.setEnabled(has_sessions)
        for row, s in enumerate(sessions):
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(s["student_name"]))
            self.table.setItem(row, 1, QTableWidgetItem(s["ip"]))
            self.table.setItem(row, 2, QTableWidgetItem(s["test_name"]))
            self.table.setItem(row, 3, QTableWidgetItem(str(s["started_at"])))
            self.table.setItem(row, 4, QTableWidgetItem(s["status"]))
            if fetch_answers:
                prog_widget = ProgressWidget(s["total_questions"], s["progress_answers"])
                self.table.setCellWidget(row, 5, prog_widget)
            else:
                self.table.removeCellWidget(row, 5)
                self.table.setItem(row, 5, QTableWidgetItem(f"{len(s['progress_answers'])}/{s['total_questions']} (Вимкнено)"))
            # Зберігаємо дані в Item
            item = self.table.item(row, 0)
            item.setData(Qt.ItemDataRole.UserRole, s)

            self.table.setItem(row, 6, QTableWidgetItem(str(s["last_activity"])))

        # Оновлюємо також каталог тестів
        self.refresh_tests()

    def closeEvent(self, event):
        """Перехоплюємо закриття вікна, щоб згорнути в трей."""
        if self.server_manager and self.server_manager.is_running:
            reply = QMessageBox.question(
                self, 'Згортання',
                'Сервер продовжує працювати. Згорнути програму в системний трей?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                event.ignore()
                self.hide()
                self.tray_manager.show_message(
                    "ШколярТест — Менеджер сервера", 
                    "Програма згорнута і працює у фоні."
                )
                return
                
        # Якщо сервер зупинено або вибрано "Ні" - закриваємо
        self.stop_server()
        event.accept()

    def load_settings(self):
        pass

    def show_context_menu(self, pos):
        item = self.table.itemAt(pos)
        if not item:
            return
        row = item.row()
        name_item = self.table.item(row, 0)
        s_data = name_item.data(Qt.ItemDataRole.UserRole)
        
        if not s_data:
            return
            
        menu = QMenu(self)
        raw_status = s_data.get("raw_status")
        
        action_pause = QAction("⏸ Призупинити", self)
        action_resume = QAction("▶ Відновити", self)
        action_stop = QAction("⏹ Зупинити", self)
        
        if raw_status == 'in_progress':
            menu.addAction(action_pause)
            menu.addAction(action_stop)
        elif raw_status == 'paused':
            menu.addAction(action_resume)
            menu.addAction(action_stop)
            
        if menu.actions():
            action = menu.exec(self.table.viewport().mapToGlobal(pos))
            if action:
                act_str = None
                if action == action_pause: act_str = "pause"
                elif action == action_resume: act_str = "resume"
                elif action == action_stop: act_str = "stop"
                
                if act_str:
                    base_url = self.lbl_full_address.text()
                    att_id = s_data["attempt_id"]
                    t_id = s_data["teacher_id"]
                    t_user = s_data["teacher_username"]
                    
                    success = ApiClient.control_attempt(base_url, att_id, act_str, t_id, t_user)
                    if success:
                        self.log_event(f"Дію '{act_str}' застосовано до учня {s_data['student_name']}.")
                        self.refresh_statistics()
                    else:
                        self.log_event(f"Помилка застосування дії '{act_str}' до учня {s_data['student_name']}.")

    def change_theme(self, theme_name: str):
        self.settings.set_theme(theme_name)
        
        import os
        base_dir = os.path.dirname(os.path.abspath(__file__))
        if theme_name == "Світла":
            qss_file = os.path.join(base_dir, "styles", "light.qss")
        else:
            qss_file = os.path.join(base_dir, "styles", "dark.qss")
            
        if os.path.exists(qss_file):
            with open(qss_file, "r", encoding="utf-8") as f:
                QApplication.instance().setStyleSheet(f.read())

    def toggle_pause_all(self):
        action = self.btn_pause_toggle.property("action")
        if action:
            self.mass_action(action)

    def mass_action(self, action: str):
        if not self.server_manager or not self.server_manager.is_running:
            return
            
        action_names = {'pause': 'призупинити', 'resume': 'відновити', 'stop': 'зупинити'}
        reply = QMessageBox.question(
            self, 'Підтвердження',
            f'Ви впевнені, що хочете {action_names.get(action)} всі видимі сесії?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
            
        base_url = self.lbl_full_address.text()
        success_count = 0
        
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 0)
            if not name_item: continue
            s_data = name_item.data(Qt.ItemDataRole.UserRole)
            if not s_data: continue
            
            raw_status = s_data.get("raw_status")
            if action == 'pause' and raw_status != 'in_progress': continue
            if action == 'resume' and raw_status != 'paused': continue
            if action == 'stop' and raw_status not in ['in_progress', 'paused']: continue
            
            att_id = s_data["attempt_id"]
            t_id = s_data["teacher_id"]
            t_user = s_data["teacher_username"]
            
            if ApiClient.control_attempt(base_url, att_id, action, t_id, t_user):
                success_count += 1
                
        if success_count > 0:
            self.log_event(f"Масову дію '{action}' успішно застосовано до {success_count} сесій.")
            self.refresh_statistics()
        else:
            self.log_event(f"Немає підходящих сесій для дії '{action}' або виникла помилка.")

    def refresh_tests(self):
        """Оновлює таблицю тестів з бази даних."""
        if not self.server_manager or not self.server_manager.is_running:
            return
            
        teacher_id = self.combo_teacher.currentData()
        tests = self.session_manager.get_tests(teacher_id)
        self.tests_table.setRowCount(0)
        
        for row, t in enumerate(tests):
            self.tests_table.insertRow(row)
            self.tests_table.setItem(row, 0, QTableWidgetItem(str(t["id"])))
            
            title_item = QTableWidgetItem(t["title"])
            title_item.setData(Qt.ItemDataRole.UserRole, t)
            self.tests_table.setItem(row, 1, title_item)
            
            self.tests_table.setItem(row, 2, QTableWidgetItem(t["subject"]))
            self.tests_table.setItem(row, 3, QTableWidgetItem(t["class_name"]))
            self.tests_table.setItem(row, 4, QTableWidgetItem(str(t["total_questions"])))
            self.tests_table.setItem(row, 5, QTableWidgetItem(t["teacher_name"]))
            
            active_txt = f"🟢 {t['active_sessions']} активна" if t['active_sessions'] > 0 else "—"
            self.tests_table.setItem(row, 6, QTableWidgetItem(active_txt))

    def _get_selected_test(self):
        selected_rows = self.tests_table.selectedItems()
        if not selected_rows:
            return None
        row = selected_rows[0].row()
        title_item = self.tests_table.item(row, 1)
        if title_item:
            return title_item.data(Qt.ItemDataRole.UserRole)
        return None

    def open_create_test_browser(self):
        base_url = self.lbl_full_address.text()
        webbrowser.open(f"{base_url}/teacher/tests/create")
        self.log_event("Відкрито сторінку створення тесту в браузері.")

    def launch_test_direct(self):
        test_data = self._get_selected_test()
        if not test_data:
            QMessageBox.warning(self, "Увага", "Будь ласка, оберіть тест у таблиці для запуску сесії.")
            return
            
        reply = QMessageBox.question(
            self, 'Запуск сесії',
            f"Запустити нову тестову сесію для тесту '{test_data['title']}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
            
        res = self.session_manager.create_session(test_data['id'])
        if res.get("success"):
            code = res["access_code"]
            QMessageBox.information(
                self, "Сесію запущено",
                f"🎉 Сесію успішно запущено!\n\n"
                f"Тест: {test_data['title']}\n"
                f"🔑 КОД ДОСТУПУ:  {code}\n\n"
                f"Учні тепер можуть заходити на сайт і вводити цей код."
            )
            self.log_event(f"Запущено сесію для тесту '{test_data['title']}', Код доступу: {code}")
            # Переключаємося на вкладку Активні сесії
            self.tab_widget.setCurrentIndex(0)
            self.refresh_statistics()
        else:
            QMessageBox.warning(self, "Помилка запуску", res.get("error", "Невідома помилка"))

    def open_edit_test_browser(self):
        test_data = self._get_selected_test()
        base_url = self.lbl_full_address.text()
        if test_data:
            webbrowser.open(f"{base_url}/teacher/tests/{test_data['id']}/edit")
            self.log_event(f"Відкрито редагування тесту '{test_data['title']}' у браузері.")
        else:
            webbrowser.open(f"{base_url}/teacher/tests")
            self.log_event("Відкрито список тестів у браузері.")

    def show_test_results_dialog(self, test_data_override=None):
        test_data = test_data_override or self._get_selected_test()
        if not test_data:
            reply = QMessageBox.question(
                self, "Результати",
                "Тест не обрано в таблиці. Відкрити загальну сторінку результатів у браузері?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            if reply == QMessageBox.StandardButton.Yes:
                webbrowser.open(f"{self.lbl_full_address.text()}/teacher/results")
            return
            
        summary = self.session_manager.get_test_results_summary(test_data["id"])
        dialog = TestResultsDialog(self, summary, self.lbl_full_address.text())
        dialog.exec()

    def on_test_double_clicked(self, item):
        row = item.row()
        title_item = self.tests_table.item(row, 1)
        if title_item:
            test_data = title_item.data(Qt.ItemDataRole.UserRole)
            if test_data:
                base_url = self.lbl_full_address.text()
                webbrowser.open(f"{base_url}/teacher/tests/{test_data['id']}/edit")

    def show_tests_context_menu(self, pos):
        item = self.tests_table.itemAt(pos)
        if not item:
            return
        row = item.row()
        title_item = self.tests_table.item(row, 1)
        if not title_item:
            return
        t_data = title_item.data(Qt.ItemDataRole.UserRole)
        if not t_data:
            return
            
        menu = QMenu(self)
        act_launch = QAction("▶ Запустити сесію (в додатку)", self)
        act_edit = QAction("✏ Редагувати (в браузері)", self)
        act_view = QAction("👁 Переглянути тест (в браузері)", self)
        act_results = QAction("📊 Результати тесту (у вікні)", self)
        
        menu.addAction(act_launch)
        menu.addAction(act_edit)
        menu.addAction(act_view)
        menu.addAction(act_results)
        
        action = menu.exec(self.tests_table.viewport().mapToGlobal(pos))
        base_url = self.lbl_full_address.text()
        test_id = t_data["id"]
        
        if action == act_launch:
            res = self.session_manager.create_session(test_id)
            if res.get("success"):
                code = res["access_code"]
                QMessageBox.information(
                    self, "Сесію запущено",
                    f"🎉 Сесію успішно запущено!\n\n"
                    f"Тест: {t_data['title']}\n"
                    f"🔑 КОД ДОСТУПУ:  {code}\n\n"
                    f"Учні тепер можуть заходити на сайт і вводити цей код."
                )
                self.log_event(f"Запущено сесію для тесту '{t_data['title']}', Код доступу: {code}")
                self.tab_widget.setCurrentIndex(0)
                self.refresh_statistics()
            else:
                QMessageBox.warning(self, "Помилка запуску", res.get("error", "Невідома помилка"))
        elif action == act_edit:
            webbrowser.open(f"{base_url}/teacher/tests/{test_id}/edit")
        elif action == act_view:
            webbrowser.open(f"{base_url}/teacher/tests/{test_id}")
        elif action == act_results:
            self.show_test_results_dialog(test_data_override=t_data)

    def on_teacher_changed(self):
        teacher_id = self.combo_teacher.currentData()
        if teacher_id is not None:
            self.settings.set_last_teacher_id(teacher_id)
        else:
            self.settings.set_last_teacher_id(0) # 0 means "Всі вчителі"
        self.refresh_statistics()

    def open_register_window(self):
        """Відкриває окреме вікно реєстрації та перегляду користувачів."""
        from gui.register_window import RegisterWindow
        if not hasattr(self, "_register_window") or self._register_window is None:
            self._register_window = RegisterWindow()
        self._register_window.show()
        self._register_window.raise_()
        self._register_window.activateWindow()


