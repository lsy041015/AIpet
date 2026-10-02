"""User-entered renewal dates; quota resets are never used as billing dates."""
from calendar import monthrange
from datetime import date
from PySide6.QtCore import QDate, QLocale, Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLabel, QDateEdit,
                               QComboBox, QDialogButtonBox, QCalendarWidget)


PERIODS = {"monthly": "매월", "yearly": "매년", "once": "반복 없음"}


def next_payment(record, today=None):
    today = today or date.today()
    try:
        anchor = date.fromisoformat(record["date"])
        period = record["period"]
        if period not in PERIODS:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    if period == "once" or anchor >= today:
        return anchor
    if period == "yearly":
        year, month = today.year, anchor.month
    else:
        year, month = today.year, today.month
    candidate = date(year, month, min(anchor.day, monthrange(year, month)[1]))
    if candidate < today:
        if period == "yearly":
            year += 1
        else:
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        candidate = date(year, month, min(anchor.day, monthrange(year, month)[1]))
    return candidate


def payment_summary(record, today=None):
    today = today or date.today()
    upcoming = next_payment(record, today)
    if upcoming is None:
        return "다음 결제일 미설정"
    days = (upcoming - today).days
    timing = "오늘" if days == 0 else f"{days}일 후" if days > 0 else "확인 필요"
    return f"다음 결제 · {upcoming:%Y.%m.%d} · {timing}"


class BillingDialog(QDialog):
    def __init__(self, parent, title, record=None):
        super().__init__(parent)
        self.clear = False
        self.setWindowTitle(title + " · 결제일 설정")
        self.setMinimumWidth(390)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        heading = QLabel(title + " 결제일")
        heading.setObjectName("eventHeading")
        layout.addWidget(heading)
        hint = QLabel("구독 화면에서 확인한 결제일을 입력하세요.\n설정한 주기에 따라 다음 결제 예정일을 표시합니다.")
        hint.setObjectName("caption")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        form.setSpacing(12)
        anchor = QDate.fromString((record or {}).get("date", ""), Qt.ISODate)
        self.date_input = QDateEdit(anchor if anchor.isValid() else QDate.currentDate())
        self.date_input.setDisplayFormat("yyyy.MM.dd")
        self.date_input.setCalendarPopup(True)
        self.date_input.setAccessibleName("결제 기준일")
        calendar = QCalendarWidget(self.date_input)
        calendar.setLocale(QLocale(QLocale.Korean, QLocale.SouthKorea))
        calendar.setFirstDayOfWeek(Qt.Monday)
        calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        calendar.setMinimumSize(330, 280)
        self.date_input.setCalendarWidget(calendar)
        self.period_input = QComboBox()
        self.period_input.setAccessibleName("결제 주기")
        for key, text in PERIODS.items():
            self.period_input.addItem(text, key)
        index = self.period_input.findData((record or {}).get("period", "monthly"))
        self.period_input.setCurrentIndex(max(0, index))
        form.addRow("결제 기준일", self.date_input)
        form.addRow("결제 주기", self.period_input)
        layout.addLayout(form)
        note = QLabel("직접 설정한 예정일입니다. 말일이 없는 달은 해당 월의 마지막 날로 표시합니다.")
        note.setWordWrap(True)
        note.setObjectName("caption")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("저장")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        remove = buttons.addButton("설정 삭제", QDialogButtonBox.ResetRole)
        remove.setEnabled(bool(record))
        remove.clicked.connect(self.remove)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def remove(self):
        self.clear = True
        self.accept()

    def selection(self):
        return None if self.clear else {"date": self.date_input.date().toString(Qt.ISODate),
                                       "period": self.period_input.currentData()}
