import os
import sys
import csv
from datetime import datetime
from typing import List, Optional

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QPushButton, QComboBox, QLineEdit, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QDialog, QDialogButtonBox, QFileDialog, QSplitter, QFrame,
    QApplication, QMenu
)
from PyQt6.QtCore import Qt, pyqtSlot, QTimer
from PyQt6.QtGui import QIcon, QFont, QAction, QColor

from app.database import SessionLocal
from app import crud, models
from gui.settings_manager import SettingsManager

POPULAR_SUBJECTS = [
    "Інформатика",
    "Математика",
    "Алгебра",
    "Геометрія",
    "Фізика",
    "Хімія",
    "Біологія",
    "Історія України",
    "Всесвітня історія",
    "Українська мова",
    "Українська література",
    "Англійська мова",
    "Німецька мова",
    "Географія",
    "Зарубіжна література",
    "Правознавство",
    "Мистецтво",
    "Технології",
    "Захист України",
    "Фізична культура",
]


class ChangePasswordDialog(QDialog):
    """Діалогове вікно швидкої зміни пароля користувача."""
    def __init__(self, username: str, parent=None):
        super().__init__(parent)
        self.username = username
        self.setWindowTitle(f"Зміна пароля: {username}")
        self.setFixedWidth(380)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        lbl_info = QLabel(f"Введіть новий пароль для користувача <b>{self.username}</b>:")
        layout.addWidget(lbl_info)

        form_layout = QGridLayout()
        form_layout.setSpacing(8)

        self.edit_new_pwd = QLineEdit()
        self.edit_new_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_new_pwd.setPlaceholderText("Мінімум 4 символи")

        self.edit_confirm_pwd = QLineEdit()
        self.edit_confirm_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_confirm_pwd.setPlaceholderText("Повторіть новий пароль")

        self.btn_toggle_eye = QPushButton("👁️ Показати")
        self.btn_toggle_eye.setFixedHeight(28)
        self.btn_toggle_eye.clicked.connect(self.toggle_password_visibility)

        form_layout.addWidget(QLabel("Новий пароль:"), 0, 0)
        form_layout.addWidget(self.edit_new_pwd, 0, 1)
        form_layout.addWidget(QLabel("Підтвердження:"), 1, 0)
        form_layout.addWidget(self.edit_confirm_pwd, 1, 1)
        form_layout.addWidget(self.btn_toggle_eye, 2, 1)

        layout.addLayout(form_layout)

        self.lbl_error = QLabel("")
        self.lbl_error.setStyleSheet("color: #f38ba8; font-weight: bold;")
        self.lbl_error.setWordWrap(True)
        layout.addWidget(self.lbl_error)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.validate_and_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def toggle_password_visibility(self):
        if self.edit_new_pwd.echoMode() == QLineEdit.EchoMode.Password:
            self.edit_new_pwd.setEchoMode(QLineEdit.EchoMode.Normal)
            self.edit_confirm_pwd.setEchoMode(QLineEdit.EchoMode.Normal)
            self.btn_toggle_eye.setText("🙈 Приховати")
        else:
            self.edit_new_pwd.setEchoMode(QLineEdit.EchoMode.Password)
            self.edit_confirm_pwd.setEchoMode(QLineEdit.EchoMode.Password)
            self.btn_toggle_eye.setText("👁️ Показати")

    def validate_and_accept(self):
        p1 = self.edit_new_pwd.text().strip()
        p2 = self.edit_confirm_pwd.text().strip()

        if len(p1) < 4:
            self.lbl_error.setText("Пароль повинен містити щонайменше 4 символи!")
            return
        if p1 != p2:
            self.lbl_error.setText("Паролі не співпадають!")
            return

        self.accept()

    def get_password(self) -> str:
        return self.edit_new_pwd.text().strip()


class RegisterWindow(QMainWindow):
    """
    Головне вікно реєстрації та перегляду зареєстрованих користувачів (PyQt6).
    Працює кросплатформенно на Linux та Windows.
    """
    def __init__(self):
        super().__init__()
        self.settings = SettingsManager()
        self.init_ui()
        self.load_theme()
        self.load_users()

    def init_ui(self):
        self.setWindowTitle("ШколярТест — Реєстрація та користувачі")
        self.resize(1080, 680)
        self.setMinimumSize(850, 550)

        # Спроба встановити іконку
        icon_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static", "favicon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        central_widget = QWidget()
        central_widget.setObjectName("centralwidget")
        self.setCentralWidget(central_widget)

        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(10)

        # --- Верхня панель (Заголовок, лічильник, перемикач теми) ---
        top_bar = QHBoxLayout()
        lbl_title = QLabel("👥 Реєстрація та керування користувачами")
        title_font = QFont()
        title_font.setPointSize(13)
        title_font.setBold(True)
        lbl_title.setFont(title_font)
        top_bar.addWidget(lbl_title)

        top_bar.addStretch()

        self.lbl_user_count = QLabel("Всього зареєстровано: 0")
        self.lbl_user_count.setStyleSheet("font-weight: bold; color: #89b4fa;")
        top_bar.addWidget(self.lbl_user_count)

        top_bar.addSpacing(15)

        lbl_theme = QLabel("Тема:")
        self.combo_theme = QComboBox()
        self.combo_theme.addItems(["Темна", "Світла"])
        self.combo_theme.setCurrentText(self.settings.get_theme())
        self.combo_theme.currentTextChanged.connect(self.change_theme)
        top_bar.addWidget(lbl_theme)
        top_bar.addWidget(self.combo_theme)

        root_layout.addLayout(top_bar)

        # --- Головний розділювач (Спліттер: Ліва частина - Форма, Права частина - Таблиця) ---
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # ================= ЛІВА ЧАСТИНА: ФОРМА РЕЄСТРАЦІЇ =================
        form_group = QGroupBox("➕ Реєстрація нового користувача")
        form_group.setMinimumWidth(340)
        form_layout = QVBoxLayout(form_group)
        form_layout.setSpacing(10)

        grid = QGridLayout()
        grid.setSpacing(8)

        # 1. Логін
        grid.addWidget(QLabel("Логін (Username) * :"), 0, 0)
        self.edit_username = QLineEdit()
        self.edit_username.setPlaceholderText("наприклад: teacher_ivanov")
        self.edit_username.textChanged.connect(self.validate_username_live)
        grid.addWidget(self.edit_username, 0, 1)

        self.lbl_username_status = QLabel("")
        self.lbl_username_status.setStyleSheet("font-size: 11px;")
        grid.addWidget(self.lbl_username_status, 1, 1)

        # 2. ПІБ
        grid.addWidget(QLabel("ПІБ користувача * :"), 2, 0)
        self.edit_fullname = QLineEdit()
        self.edit_fullname.setPlaceholderText("Іванов Іван Іванович")
        grid.addWidget(self.edit_fullname, 2, 1)

        # 3. Пароль
        grid.addWidget(QLabel("Пароль * :"), 3, 0)
        self.edit_password = QLineEdit()
        self.edit_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_password.setPlaceholderText("Мінімум 4 символи")
        grid.addWidget(self.edit_password, 3, 1)

        # 4. Підтвердження пароля
        grid.addWidget(QLabel("Підтвердження * :"), 4, 0)
        self.edit_password_confirm = QLineEdit()
        self.edit_password_confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_password_confirm.setPlaceholderText("Повторіть пароль")
        grid.addWidget(self.edit_password_confirm, 4, 1)

        # Кнопка Показати/Сховати паролі
        self.btn_show_pwd = QPushButton("👁️ Показати пароль")
        self.btn_show_pwd.setFixedHeight(28)
        self.btn_show_pwd.clicked.connect(self.toggle_password_visibility)
        grid.addWidget(self.btn_show_pwd, 5, 1)

        # 5. Предмет
        grid.addWidget(QLabel("Предмет :"), 6, 0)
        self.combo_subject = QComboBox()
        self.combo_subject.setEditable(True)
        self.combo_subject.addItem("— Не вказано —", "")
        for subj in POPULAR_SUBJECTS:
            self.combo_subject.addItem(subj, subj)
        grid.addWidget(self.combo_subject, 6, 1)

        # 6. Класи
        grid.addWidget(QLabel("Закріплені класи :"), 7, 0)
        self.edit_classes = QLineEdit()
        self.edit_classes.setPlaceholderText("наприклад: 5-А, 7-Б, 10-В")
        grid.addWidget(self.edit_classes, 7, 1)

        # 7. Активний
        self.chk_active = QCheckBox("Обліковий запис активний")
        self.chk_active.setChecked(True)
        grid.addWidget(self.chk_active, 8, 1)

        form_layout.addLayout(grid)

        # Статус повідомлення форми
        self.lbl_form_msg = QLabel("")
        self.lbl_form_msg.setWordWrap(True)
        self.lbl_form_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_form_msg.setStyleSheet("font-weight: bold; padding: 4px;")
        form_layout.addWidget(self.lbl_form_msg)

        # Кнопки форми
        btn_layout = QHBoxLayout()
        self.btn_register = QPushButton("🚀 Зареєструвати")
        self.btn_register.setObjectName("btn_register")
        self.btn_register.setStyleSheet(
            "QPushButton#btn_register { background-color: #a6e3a1; color: #11111b; font-weight: bold; padding: 10px; }"
            "QPushButton#btn_register:hover { background-color: #8bd5ca; }"
        )
        self.btn_register.clicked.connect(self.handle_register)

        self.btn_clear = QPushButton("🧹 Очистити")
        self.btn_clear.clicked.connect(self.clear_form)

        btn_layout.addWidget(self.btn_register)
        btn_layout.addWidget(self.btn_clear)
        form_layout.addLayout(btn_layout)

        form_layout.addStretch()
        splitter.addWidget(form_group)

        # ================= ПРАВА ЧАСТИНА: СПИСОК КОРИСТУВАЧІВ =================
        table_group = QGroupBox("📋 Список зареєстрованих користувачів")
        table_layout = QVBoxLayout(table_group)
        table_layout.setSpacing(8)

        # Рядок пошуку та фільтрації
        search_layout = QHBoxLayout()
        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText("🔍 Пошук за логіном, ПІБ, предметом чи класом...")
        self.edit_search.textChanged.connect(self.filter_users_table)
        search_layout.addWidget(self.edit_search)

        btn_refresh = QPushButton("🔄 Оновити")
        btn_refresh.clicked.connect(self.load_users)
        search_layout.addWidget(btn_refresh)
        table_layout.addLayout(search_layout)

        # Таблиця
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "ID", "Логін", "ПІБ", "Предмет", "Класи", "Статус", "Створено", "Тестів"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        table_layout.addWidget(self.table)

        # Дії з виділеним користувачем
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        self.btn_change_pwd = QPushButton("🔑 Змінити пароль")
        self.btn_change_pwd.clicked.connect(self.action_change_password)

        self.btn_copy = QPushButton("📋 Скопіювати дані")
        self.btn_copy.clicked.connect(self.action_copy_user_info)

        self.btn_export = QPushButton("📤 Експорт у CSV")
        self.btn_export.clicked.connect(self.action_export_csv)

        self.btn_delete = QPushButton("🗑️ Видалити")
        self.btn_delete.setStyleSheet(
            "QPushButton { background-color: #313244; color: #f38ba8; }"
            "QPushButton:hover { background-color: #f38ba8; color: #11111b; }"
        )
        self.btn_delete.clicked.connect(self.action_delete_user)

        actions_layout.addWidget(self.btn_change_pwd)
        actions_layout.addWidget(self.btn_copy)
        actions_layout.addWidget(self.btn_export)
        actions_layout.addStretch()
        actions_layout.addWidget(self.btn_delete)

        table_layout.addLayout(actions_layout)
        splitter.addWidget(table_group)

        # Встановлюємо пропорції спліттера (35% зліва, 65% справа)
        splitter.setStretchFactor(0, 35)
        splitter.setStretchFactor(1, 65)

        root_layout.addWidget(splitter)

    def load_theme(self):
        theme_name = self.settings.get_theme()
        style_file = "light.qss" if theme_name == "Світла" else "dark.qss"
        style_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "styles", style_file)
        if os.path.exists(style_path):
            with open(style_path, "r", encoding="utf-8") as f:
                self.setStyleSheet(f.read())

    def change_theme(self, theme_name: str):
        self.settings.set_theme(theme_name)
        self.load_theme()

    def toggle_password_visibility(self):
        if self.edit_password.echoMode() == QLineEdit.EchoMode.Password:
            self.edit_password.setEchoMode(QLineEdit.EchoMode.Normal)
            self.edit_password_confirm.setEchoMode(QLineEdit.EchoMode.Normal)
            self.btn_show_pwd.setText("🙈 Приховати пароль")
        else:
            self.edit_password.setEchoMode(QLineEdit.EchoMode.Password)
            self.edit_password_confirm.setEchoMode(QLineEdit.EchoMode.Password)
            self.btn_show_pwd.setText("👁️ Показати пароль")

    def validate_username_live(self, text: str):
        username = text.strip()
        if not username:
            self.lbl_username_status.setText("")
            return
        if len(username) < 3:
            self.lbl_username_status.setText("⚠️ Занадто короткий (мін. 3 симв.)")
            self.lbl_username_status.setStyleSheet("color: #fab387; font-size: 11px;")
            return

        # Перевірка унікальності в БД
        db = SessionLocal()
        try:
            existing = crud.get_teacher_by_username(db, username)
            if existing:
                self.lbl_username_status.setText("❌ Логін вже зайнятий!")
                self.lbl_username_status.setStyleSheet("color: #f38ba8; font-size: 11px; font-weight: bold;")
            else:
                self.lbl_username_status.setText("✅ Логін вільний")
                self.lbl_username_status.setStyleSheet("color: #a6e3a1; font-size: 11px;")
        except Exception:
            pass
        finally:
            db.close()

    def handle_register(self):
        username = self.edit_username.text().strip()
        fullname = self.edit_fullname.text().strip()
        pwd = self.edit_password.text().strip()
        pwd_confirm = self.edit_password_confirm.text().strip()
        subject = self.combo_subject.currentText().strip()
        if subject == "— Не вказано —":
            subject = None
        classes = self.edit_classes.text().strip() or None
        is_active = self.chk_active.isChecked()

        # Валідація
        if not username or not fullname or not pwd:
            self.show_form_message("Будь ласка, заповніть обов'язкові поля (*)", is_error=True)
            return

        if len(username) < 3:
            self.show_form_message("Логін має містити щонайменше 3 символи!", is_error=True)
            return

        if len(pwd) < 4:
            self.show_form_message("Пароль має містити щонайменше 4 символи!", is_error=True)
            return

        if pwd != pwd_confirm:
            self.show_form_message("Введені паролі не співпадають!", is_error=True)
            return

        db = SessionLocal()
        try:
            existing = crud.get_teacher_by_username(db, username)
            if existing:
                self.show_form_message(f"Користувач з логіном '{username}' вже існує!", is_error=True)
                return

            new_teacher = crud.create_teacher(
                db=db,
                username=username,
                full_name=fullname,
                password=pwd,
                subject=subject,
                classes=classes,
                is_active=is_active
            )
            self.show_form_message(f"✅ Користувача '{username}' ({fullname}) успішно створено!", is_error=False)
            self.clear_form()
            self.load_users()
        except Exception as e:
            self.show_form_message(f"Помилка створення: {str(e)}", is_error=True)
        finally:
            db.close()

    def show_form_message(self, message: str, is_error: bool = False):
        color = "#f38ba8" if is_error else "#a6e3a1"
        self.lbl_form_msg.setText(message)
        self.lbl_form_msg.setStyleSheet(f"color: {color}; font-weight: bold; padding: 4px;")
        QTimer.singleShot(6000, lambda: self.lbl_form_msg.setText(""))

    def clear_form(self):
        self.edit_username.clear()
        self.edit_fullname.clear()
        self.edit_password.clear()
        self.edit_password_confirm.clear()
        self.combo_subject.setCurrentIndex(0)
        self.edit_classes.clear()
        self.chk_active.setChecked(True)
        self.lbl_username_status.setText("")

    def load_users(self):
        """Завантажує та відображає всіх зареєстрованих користувачів із бази даних."""
        db = SessionLocal()
        try:
            teachers = crud.get_all_teachers(db)
            self.all_users_data = []

            for t in teachers:
                tests_count = len(t.tests) if hasattr(t, "tests") and t.tests else 0
                created_str = t.created_at.strftime("%d.%m.%Y %H:%M") if t.created_at else "—"
                self.all_users_data.append({
                    "id": t.id,
                    "username": t.username,
                    "full_name": t.full_name,
                    "subject": t.subject or "—",
                    "classes": t.classes or "—",
                    "is_active": t.is_active,
                    "created_at": created_str,
                    "tests_count": tests_count
                })

            self.lbl_user_count.setText(f"Всього зареєстровано: {len(self.all_users_data)}")
            self.render_table(self.all_users_data)
        except Exception as e:
            print(f"Помилка завантаження користувачів: {e}")
        finally:
            db.close()

    def render_table(self, users: List[dict]):
        self.table.setRowCount(len(users))
        for row, u in enumerate(users):
            item_id = QTableWidgetItem(str(u["id"]))
            item_id.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_id.setData(Qt.ItemDataRole.UserRole, u["id"])

            item_username = QTableWidgetItem(u["username"])
            item_username.setFont(QFont("", -1, QFont.Weight.Bold))

            item_fullname = QTableWidgetItem(u["full_name"])
            item_subject = QTableWidgetItem(u["subject"])
            item_classes = QTableWidgetItem(u["classes"])

            status_text = "🟢 Активний" if u["is_active"] else "🔴 Заблокований"
            item_status = QTableWidgetItem(status_text)
            item_status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            item_created = QTableWidgetItem(u["created_at"])
            item_created.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            item_tests = QTableWidgetItem(str(u["tests_count"]))
            item_tests.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            self.table.setItem(row, 0, item_id)
            self.table.setItem(row, 1, item_username)
            self.table.setItem(row, 2, item_fullname)
            self.table.setItem(row, 3, item_subject)
            self.table.setItem(row, 4, item_classes)
            self.table.setItem(row, 5, item_status)
            self.table.setItem(row, 6, item_created)
            self.table.setItem(row, 7, item_tests)

    def filter_users_table(self, text: str):
        query = text.strip().lower()
        if not hasattr(self, "all_users_data"):
            return
        if not query:
            self.render_table(self.all_users_data)
            return

        filtered = [
            u for u in self.all_users_data
            if query in u["username"].lower()
            or query in u["full_name"].lower()
            or query in u["subject"].lower()
            or query in u["classes"].lower()
        ]
        self.render_table(filtered)

    def get_selected_user_id_and_username(self) -> Optional[tuple]:
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return None
        row = selected_rows[0].row()
        item_id = self.table.item(row, 0)
        item_username = self.table.item(row, 1)
        if not item_id or not item_username:
            return None
        return int(item_id.text()), item_username.text()

    def action_change_password(self):
        selected = self.get_selected_user_id_and_username()
        if not selected:
            QMessageBox.information(self, "Вибір користувача", "Будь ласка, виберіть користувача у таблиці!")
            return
        teacher_id, username = selected
        dialog = ChangePasswordDialog(username=username, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_pwd = dialog.get_password()
            db = SessionLocal()
            try:
                updated = crud.update_teacher_password(db, teacher_id, new_pwd)
                if updated:
                    QMessageBox.information(
                        self, "Успіх", f"Пароль для користувача '{username}' успішно змінено!"
                    )
                else:
                    QMessageBox.warning(self, "Помилка", "Не вдалося оновити пароль!")
            except Exception as e:
                QMessageBox.critical(self, "Помилка", f"Помилка при зміні пароля: {e}")
            finally:
                db.close()

    def action_copy_user_info(self):
        selected = self.get_selected_user_id_and_username()
        if not selected:
            QMessageBox.information(self, "Вибір користувача", "Будь ласка, виберіть користувача у таблиці!")
            return
        teacher_id, username = selected
        row = self.table.selectionModel().selectedRows()[0].row()
        fullname = self.table.item(row, 2).text()
        subject = self.table.item(row, 3).text()
        classes = self.table.item(row, 4).text()

        text_to_copy = f"Логін: {username}\nПІБ: {fullname}\nПредмет: {subject}\nКласи: {classes}"
        clipboard = QApplication.clipboard()
        clipboard.setText(text_to_copy)
        QMessageBox.information(self, "Скопійовано", f"Дані користувача '{username}' скопійовано в буфер обміну!")

    def action_export_csv(self):
        if not hasattr(self, "all_users_data") or not self.all_users_data:
            QMessageBox.information(self, "Експорт", "Список користувачів порожній!")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Експорт користувачів у CSV", "users_list.csv", "CSV файли (*.csv);;Всі файли (*)"
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(["ID", "Логін", "ПІБ", "Предмет", "Класи", "Статус", "Дата реєстрації", "Кількість тестів"])
                for u in self.all_users_data:
                    status = "Активний" if u["is_active"] else "Заблокований"
                    writer.writerow([
                        u["id"], u["username"], u["full_name"], u["subject"],
                        u["classes"], status, u["created_at"], u["tests_count"]
                    ])
            QMessageBox.information(self, "Успіх", f"Дані успішно експортовано у файл:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Помилка експорту", f"Не вдалося зберегти файл: {e}")

    def action_delete_user(self):
        selected = self.get_selected_user_id_and_username()
        if not selected:
            QMessageBox.information(self, "Вибір користувача", "Будь ласка, виберіть користувача для видалення!")
            return
        teacher_id, username = selected

        if len(self.all_users_data) <= 1:
            QMessageBox.warning(
                self, "Заборонено",
                "Неможливо видалити єдиного користувача в системі! В системі повинен залишатися щонайменше один обліковий запис."
            )
            return

        reply = QMessageBox.question(
            self, "Підтвердження видалення",
            f"Ви дійсно бажаєте видалити користувача <b>'{username}'</b>?<br><br>"
            "<font color='#f38ba8'>Увага: всі пов'язані тести та результати можуть стати недоступними!</font>",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            db = SessionLocal()
            try:
                success = crud.delete_teacher(db, teacher_id)
                if success:
                    QMessageBox.information(self, "Видалено", f"Користувача '{username}' успішно видалено!")
                    self.load_users()
                else:
                    QMessageBox.warning(self, "Помилка", "Не вдалося знайти або видалити користувача.")
            except Exception as e:
                QMessageBox.critical(self, "Помилка", f"Помилка при видаленні: {e}")
            finally:
                db.close()

    def show_context_menu(self, pos):
        item = self.table.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)

        action_pwd = QAction("🔑 Змінити пароль", self)
        action_pwd.triggered.connect(self.action_change_password)
        menu.addAction(action_pwd)

        action_copy = QAction("📋 Скопіювати дані", self)
        action_copy.triggered.connect(self.action_copy_user_info)
        menu.addAction(action_copy)

        menu.addSeparator()

        action_del = QAction("🗑️ Видалити користувача", self)
        action_del.triggered.connect(self.action_delete_user)
        menu.addAction(action_del)

        menu.exec(self.table.viewport().mapToGlobal(pos))
