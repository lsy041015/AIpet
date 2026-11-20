"""A small local to-do list shared by the widget and desktop views."""
import json

from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QCheckBox)


class TodoPanel(QWidget):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.items = []
        self.rows = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 2, 0)
        layout.setSpacing(14)

        hint = QLabel("할 일을 적고 Enter를 누르세요. 이 PC에 저장됩니다.")
        hint.setObjectName("caption")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("새 할 일")
        self.input.setAccessibleName("새 할 일 입력")
        self.input.returnPressed.connect(self.add_item)
        form.addWidget(self.input, 1)
        self.add_button = QPushButton("추가")
        self.add_button.setObjectName("todoAdd")
        self.add_button.setAccessibleName("할 일 추가")
        self.add_button.clicked.connect(self.add_item)
        form.addWidget(self.add_button)
        layout.addLayout(form)

        self.count = QLabel()
        self.count.setObjectName("todoCount")
        layout.addWidget(self.count)
        self.message = QLabel()
        self.message.setObjectName("todoMessage")
        self.message.setWordWrap(True)
        self.message.hide()
        layout.addWidget(self.message)
        self.empty = QLabel("아직 할 일이 없습니다.")
        self.empty.setObjectName("caption")
        layout.addWidget(self.empty)
        self.list_layout = QVBoxLayout()
        self.list_layout.setSpacing(8)
        layout.addLayout(self.list_layout)
        layout.addStretch()

        try:
            data = json.loads(self.settings.value("todo/items", "[]"))
            if not isinstance(data, list) or any(
                not isinstance(item, dict) or not isinstance(item.get("text"), str)
                or not item["text"].strip() or len(item["text"]) > 200
                or type(item.get("done")) is not bool for item in data
            ):
                raise ValueError("invalid to-do data")
            self.items = [{"text": item["text"], "done": item["done"]} for item in data]
        except (TypeError, ValueError):
            self.input.setEnabled(False)
            self.add_button.setEnabled(False)
            self.message.setText("저장된 할 일 목록을 읽지 못했습니다. 설정 파일을 확인해 주세요.")
            self.message.show()
        self.render()

    def save(self, items):
        old = self.settings.value("todo/items", "[]")
        self.settings.setValue("todo/items", json.dumps(items, ensure_ascii=False))
        self.settings.sync()
        if self.settings.status() != QSettings.NoError:
            self.settings.setValue("todo/items", old)
            self.message.setText("저장 실패 · 설정 파일 권한을 확인해 주세요.")
            self.message.show()
            return False
        self.items = items
        self.message.hide()
        self.render()
        return True

    def add_item(self):
        text = self.input.text().strip()
        if not text:
            return
        if len(text) > 200 or len(self.items) >= 200:
            self.message.setText("할 일은 200자 이내, 최대 200개까지 저장할 수 있습니다.")
            self.message.show()
            return
        if self.save([{"text": text, "done": False}, *self.items]):
            self.input.clear()
            self.input.setFocus()

    def set_done(self, item, done):
        self.save([dict(task, done=done) if task is item else task for task in self.items])

    def delete_item(self, item):
        self.save([task for task in self.items if task is not item])

    def render(self):
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.rows = []
        remaining = sum(not item["done"] for item in self.items)
        self.count.setText(f"남은 할 일 {remaining}개")
        self.empty.setVisible(not self.items)
        for item in reversed(self.items):
            row = QFrame()
            row.setObjectName("todoItem")
            line = QHBoxLayout(row)
            line.setContentsMargins(12, 10, 10, 10)
            line.setSpacing(10)
            check = QCheckBox()
            check.setChecked(item["done"])
            check.setAccessibleName(("완료 취소: " if item["done"] else "완료 표시: ") + item["text"])
            check.toggled.connect(lambda done, task=item: self.set_done(task, done))
            line.addWidget(check)
            title = QLabel(item["text"])
            title.setObjectName("todoText")
            title.setTextFormat(Qt.PlainText)
            title.setWordWrap(True)
            title.setProperty("completed", item["done"])
            font = title.font()
            font.setStrikeOut(item["done"])
            title.setFont(font)
            line.addWidget(title, 1)
            delete = QPushButton("×")
            delete.setObjectName("todoDelete")
            delete.setFixedSize(28, 28)
            delete.setToolTip("할 일 삭제")
            delete.setAccessibleName("할 일 삭제: " + item["text"])
            delete.clicked.connect(lambda checked=False, task=item: self.delete_item(task))
            line.addWidget(delete)
            self.list_layout.addWidget(row)
            self.rows.append((item, check, delete, title))
