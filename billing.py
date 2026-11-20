"""User-entered renewal dates; quota resets are never used as billing dates."""
import math
from calendar import monthrange
from datetime import date
from PySide6.QtCore import QDate, QLocale, Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLabel, QDateEdit,
                               QComboBox, QDialogButtonBox, QCalendarWidget, QLineEdit)


PERIODS = {"monthly": "매월", "yearly": "매년", "once": "반복 없음"}

# Published list prices in USD per month (per seat for team plans). Taxes, annual plans
# and discounts are not included, so the real charge may differ.
LIST_PRICES = {
    ("claude", "pro"): 20, ("claude", "max_5x"): 100, ("claude", "max_20x"): 200,
    ("claude", "team_standard"): 30, ("claude", "team_premium"): 150,
    ("codex", "plus"): 20, ("codex", "pro"): 200, ("codex", "team"): 30,
    ("copilot", "individual"): 10, ("copilot", "individual_pro"): 39, ("copilot", "business"): 19,
}


def list_price(kind, plan, tier=""):
    if not isinstance(plan, str):
        return None
    if kind == "claude" and plan in ("max", "team"):
        # Claude reports Max size and Team seat type only in planTier (e.g. "max_5x").
        tier = tier if isinstance(tier, str) else ""
        names = ("max_5x", "max_20x") if plan == "max" else ("team_standard", "team_premium")
        plan = next((name for name in names if name.rpartition("_")[2] in tier), None)
    return LIST_PRICES.get((kind, plan))


def parse_price(text):
    try:
        value = float(str(text).strip().lstrip("$").replace(",", ""))
    except ValueError:
        return None
    return value if math.isfinite(value) and 0 <= value <= 100000 else None


def price_summary(manual, listed):
    if manual is not None:
        return f"월 ${manual:g} · 직접 입력"
    return f"월 ${listed:g} · 정가 기준" if listed is not None else "요금 미확인"


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
    def __init__(self, parent, title, record=None, price=None, listed=None):
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
        self.price_input = QLineEdit("" if price is None else f"{price:g}")
        self.price_input.setAccessibleName("월 요금 (USD)")
        self.price_input.setPlaceholderText(f"비우면 정가 ${listed:g}" if listed is not None else "예: 20")
        form.addRow("결제 기준일", self.date_input)
        form.addRow("결제 주기", self.period_input)
        form.addRow("월 요금 (USD)", self.price_input)
        layout.addLayout(form)
        note = QLabel("직접 설정한 예정일입니다. 말일이 없는 달은 해당 월의 마지막 날로 표시합니다.")
        note.setWordWrap(True)
        note.setObjectName("caption")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("저장")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        remove = buttons.addButton("설정 삭제", QDialogButtonBox.ResetRole)
        remove.setEnabled(bool(record) or price is not None)
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

    def price(self):
        # Empty or invalid input falls back to the list price.
        return None if self.clear else parse_price(self.price_input.text())
