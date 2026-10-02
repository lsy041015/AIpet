"""Small Calendar panel sharing the quota widget's existing theme."""
from datetime import date, datetime, timedelta
from uuid import uuid4

from PySide6.QtCore import QDate, QDateTime, QTime, Qt, QThread, QTimer, QUrl, QLocale, QSettings, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QColor, QTextCharFormat
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QDialog, QFormLayout, QLineEdit, QDateTimeEdit,
                               QCheckBox, QFileDialog, QGridLayout, QCalendarWidget, QToolButton,
                               QInputDialog, QScrollArea, QLayout, QMenu, QMessageBox, QComboBox)
from calendar_client import CalendarClient, CalendarError, event_body, event_text
from quick_event import parse_quick_event

TITLE_KEYWORDS = ("학사일정", "출장", "연차", "조기퇴근", "반차", "회의",
                  "교육", "시험", "과제 마감", "월세 날", "급여일")
BUSY_TEXT = "진행 중인 일정 조회·연결이 끝나면 다시 눌러 주세요."


def text_label(text=""):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    return label


class CalendarJob(QThread):
    def __init__(self, action, parent):
        super().__init__(parent)
        self.action = action
        self.value = None
        self.error = ""

    def run(self):
        try:
            self.value = self.action()
        except CalendarError as error:
            self.error = str(error)
        except ImportError:
            self.error = "Google 로그인 라이브러리 설치를 확인하세요."
        except Exception:
            # OAuth/network exceptions may include tokens or authorization codes.
            self.error = "연결·응답 확인 실패. 로그인 창에서 완료하거나 다시 시도하세요."


class EventDialog(QDialog):
    def __init__(self, panel, event=None):
        super().__init__(panel)
        self.panel = panel
        self.edit_target = event
        self.pending = None
        self.saving = False
        self.keyword_settings = QSettings(str(panel.client.directory / "keywords.ini"), QSettings.IniFormat)
        saved_keywords = self.keyword_settings.value("titles", list(TITLE_KEYWORDS))
        # Qt INI reads a saved one-item list back as a string and an empty list as None.
        saved_keywords = [] if saved_keywords is None else [saved_keywords] if isinstance(saved_keywords, str) else saved_keywords
        self.title_keywords = list(dict.fromkeys(saved_keywords)) if isinstance(saved_keywords, list) and all(
            isinstance(word, str) and 0 < len(word) <= 20 and not any(c in word for c in "[]\n\r")
            for word in saved_keywords) else list(TITLE_KEYWORDS)
        self.known_keywords = set(self.title_keywords)
        self.setWindowTitle("Google Calendar 일정 수정" if event else "Google Calendar 일정 추가")
        self.setObjectName("eventDialog")
        self.setMinimumWidth(440)
        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 24)
        form.setSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        heading = text_label("일정 수정 · 이번 일정만" if event and event.get("recurringEventId") else "일정 수정" if event else "새 일정")
        heading.setObjectName("eventHeading")
        form.addRow(heading)
        hint = text_label("키워드를 누르고 제목을 이어 입력하세요.")
        hint.setObjectName("caption")
        form.addRow(hint)
        self.keywords = QWidget()
        self.keyword_grid = QGridLayout(self.keywords)
        self.keyword_grid.setContentsMargins(0, 0, 0, 0)
        self.keyword_grid.setSpacing(6)
        self.keyword_buttons = {}
        self.keyword_scroll = QScrollArea()
        self.keyword_scroll.setWidgetResizable(True)
        self.keyword_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.keyword_scroll.setWidget(self.keywords)
        form.addRow(self.keyword_scroll)
        self.keyword_tools = QWidget()
        tools = QHBoxLayout(self.keyword_tools)
        tools.setContentsMargins(0, 0, 0, 0)
        for title, callback in (("+ 키워드 추가", self.add_keyword), ("키워드 삭제", self.remove_keyword)):
            button = QPushButton(title)
            button.setAutoDefault(False)
            button.clicked.connect(callback)
            tools.addWidget(button)
        form.addRow(self.keyword_tools)
        self.title_input = QLineEdit()
        self.title_input.setMaxLength(300)
        self.title_input.setPlaceholderText("예: [출장] 대전 고객사 방문")
        self.title_input.textChanged.connect(self.sync_keywords)
        self.rebuild_keywords()
        start = QDateTime.currentDateTime().addSecs(3600)
        start.setTime(QTime(start.time().hour(), start.time().minute()))  # fields hide seconds
        self.start = QDateTimeEdit(start)
        self.end = QDateTimeEdit(self.start.dateTime().addSecs(3600))
        for field in (self.start, self.end):
            field.setCalendarPopup(True)
            field.setMaximumDate(QDate(9999, 12, 30))  # an all-day end + 1 day must fit in datetime
            field.setDisplayFormat("yyyy-MM-dd  HH:mm")
            field.setMinimumHeight(40)
            calendar = QCalendarWidget(field)
            calendar.setLocale(QLocale(QLocale.Korean, QLocale.SouthKorea))
            calendar.setFirstDayOfWeek(Qt.Monday)
            calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
            calendar.setHorizontalHeaderFormat(QCalendarWidget.ShortDayNames)
            calendar.setGridVisible(False)
            calendar.setMinimumSize(360, 300)
            weekend = QTextCharFormat()
            weekend.setForeground(QColor("#b64250" if getattr(panel.window(), "theme", "dark") == "light" else "#f296a0"))
            for day in (Qt.Saturday, Qt.Sunday):
                calendar.setWeekdayTextFormat(day, weekend)
            for name, arrow in (("qt_calendar_prevmonth", "‹"), ("qt_calendar_nextmonth", "›")):
                button = calendar.findChild(QToolButton, name)
                if button:
                    button.setIcon(QIcon())
                    button.setText(arrow)
            field.setCalendarWidget(calendar)
        self.all_day = QCheckBox("종일 일정")
        self.all_day.toggled.connect(self.toggle_all_day)
        form.addRow("제목", self.title_input)
        form.addRow("시작", self.start)
        form.addRow("종료", self.end)
        form.addRow(self.all_day)
        info = text_label("PC 현지 시간 · 종일 일정은 마지막 날짜까지 선택\nGoogle 기본 캘린더에 저장됩니다.")
        info.setObjectName("caption")
        form.addRow(info)
        self.status = text_label()
        form.addRow(self.status)
        self.submit = QPushButton("일정 추가")
        self.submit.setObjectName("eventSubmit")
        self.submit.setMinimumHeight(40)
        self.submit.clicked.connect(self.save)
        form.addRow(self.submit)
        self.delete_button = QPushButton("일정 삭제")
        self.delete_button.setVisible(event is not None)
        self.delete_button.clicked.connect(self.delete_event)
        form.addRow(self.delete_button)
        if event:
            # Never truncate an existing title; the 300-character limit applies to new text.
            self.title_input.setMaxLength(max(300, len(event.get("summary", ""))))
            self.title_input.setText(event.get("summary", ""))
            all_day = "date" in event["start"]
            self.all_day.setChecked(all_day)
            for field, key in ((self.start, "start"), (self.end, "end")):
                raw = event[key].get("date") or event[key]["dateTime"]
                value = QDateTime.fromString(raw, Qt.ISODate)
                if all_day and key == "end":
                    value = value.addDays(-1)
                field.setDateTime(value.toLocalTime() if not all_day else value)
            self.submit.setText("변경 저장")

    def rebuild_keywords(self):
        while self.keyword_grid.count():
            button = self.keyword_grid.takeAt(0).widget()
            button.hide()
            button.deleteLater()
        self.keyword_buttons = {}
        for index, keyword in enumerate(self.title_keywords):
            button = QPushButton()
            button.setText(button.fontMetrics().elidedText(keyword, Qt.ElideRight, 78))
            button.setToolTip(keyword)
            button.setObjectName("keywordButton")
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setAccessibleName(keyword + " 제목 키워드")
            button.clicked.connect(lambda checked=False, word=keyword: self.apply_keyword(word))
            self.keyword_grid.addWidget(button, index // 4, index % 4)
            self.keyword_buttons[keyword] = button
        self.keyword_scroll.setFixedHeight(max(38, min(152, ((len(self.title_keywords) + 3) // 4) * 38)))
        self.sync_keywords(self.title_input.text())

    def store_keywords(self, words):
        self.keyword_settings.setValue("titles", words)
        self.keyword_settings.sync()
        if self.keyword_settings.status() != QSettings.NoError:
            self.status.setText("키워드 저장 실패. 설정 폴더 권한을 확인하세요.")
            return
        self.title_keywords = words
        self.known_keywords.update(words)
        self.rebuild_keywords()
        self.status.setText("키워드 저장됨")

    def add_keyword(self):
        word, accepted = QInputDialog.getText(self, "키워드 추가", "새 키워드 (1–20자)")
        if not accepted:
            return
        word = word.strip()
        if not 0 < len(word) <= 20 or any(c in word for c in "[]\n\r"):
            self.status.setText("대괄호·줄바꿈 없이 1–20자로 입력하세요.")
        elif word in self.title_keywords:
            self.status.setText("이미 있는 키워드입니다.")
        else:
            self.store_keywords(self.title_keywords + [word])

    def remove_keyword(self):
        if not self.title_keywords:
            self.status.setText("삭제할 키워드가 없습니다.")
            return
        word, accepted = QInputDialog.getItem(self, "키워드 삭제", "삭제할 키워드", self.title_keywords, 0, False)
        if accepted:
            self.store_keywords([item for item in self.title_keywords if item != word])

    def apply_keyword(self, keyword):
        title = self.title_input.text()
        for word in self.known_keywords:
            if title.startswith(f"[{word}]"):
                title = title[len(word) + 2:].lstrip()
                break
        value = f"[{keyword}] " + title
        if len(value) > self.title_input.maxLength():
            self.status.setText("제목이 너무 깁니다. 내용을 줄인 뒤 키워드를 선택하세요.")
            self.sync_keywords(self.title_input.text())
            return
        self.title_input.setText(value)
        self.title_input.setFocus()
        self.title_input.setCursorPosition(len(value))

    def sync_keywords(self, title):
        for keyword, button in self.keyword_buttons.items():
            button.setChecked(title.startswith(f"[{keyword}]"))

    def toggle_all_day(self, enabled):
        for field in (self.start, self.end):
            field.setDisplayFormat("yyyy-MM-dd" if enabled else "yyyy-MM-dd HH:mm")

    def save(self):
        if self.saving:
            return
        if self.panel.job:
            self.status.setText(BUSY_TEXT)
            return
        try:
            all_day = self.all_day.isChecked()
            if all_day:
                start = self.start.date().toPython()
                end = self.end.date().toPython() + timedelta(days=1)
            else:
                # The fields hide seconds, so a typed 15:00 must not keep hidden ones.
                start = datetime.fromtimestamp(self.start.dateTime().toSecsSinceEpoch()).astimezone().replace(second=0)
                end = datetime.fromtimestamp(self.end.dateTime().toSecsSinceEpoch()).astimezone().replace(second=0)
            # An event that was already untitled may be saved after changing only its time.
            required = not self.edit_target or bool(self.edit_target.get("summary", "").strip())
            body = event_body(self.title_input.text(), start, end, all_day,
                              self.title_input.maxLength(), required)
        except CalendarError as error:
            self.status.setText(str(error))
            return
        if not self.pending or body != {k: v for k, v in self.pending.items() if k != "id"}:
            self.pending = dict(body, id=uuid4().hex)
        self.saving = True
        for control in (self.title_input, self.keywords, self.keyword_tools, self.start, self.end, self.all_day, self.submit):
            control.setEnabled(False)
        self.status.setText("Google Calendar에 저장 중…")
        self.delete_button.setEnabled(False)
        self.panel.run(lambda: self.panel.client.update(self.edit_target, body) if self.edit_target
                       else self.panel.client.add(self.pending), self.saved)

    def delete_event(self):
        if self.saving or not self.edit_target:
            return
        if self.panel.job:
            self.status.setText(BUSY_TEXT)
            return
        message = "이 일정을 Google Calendar에서 삭제할까요?"
        if self.edit_target.get("recurringEventId"):
            message += "\n반복 일정 중 이번 일정만 삭제됩니다."
        if QMessageBox.question(self, "일정 삭제", message,
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.saving = True
        self.submit.setEnabled(False)
        self.delete_button.setEnabled(False)
        self.status.setText("일정 삭제 중…")
        self.panel.run(lambda: self.panel.client.delete(self.edit_target),
                       lambda value, error: self.saved(value, error, deleted=True))

    def saved(self, value, error, deleted=False):
        self.saving = False
        for control in (self.title_input, self.keywords, self.keyword_tools, self.start, self.end, self.all_day, self.submit):
            control.setEnabled(True)
        self.delete_button.setEnabled(True)
        if error:
            hint = "목록을 갱신해 결과를 확인한 뒤 다시 열어 주세요." if self.edit_target else "등록 여부가 불확실하면 같은 내용으로 재시도하세요. 중복 등록을 방지합니다."
            self.status.setText(error + "\n" + hint)
            return
        self.accept()
        self.panel.show_success("일정 삭제 완료" if deleted else "일정 수정 완료" if self.edit_target else "일정 추가 완료")
        self.panel.refresh()

    def reject(self):
        if not self.saving:
            super().reject()

    def closeEvent(self, event):
        if self.saving:
            event.ignore()
        else:
            super().closeEvent(event)


class CalendarPanel(QWidget):
    activity = Signal(int)

    def __init__(self, parent=None, client=None):
        super().__init__(parent)
        self.client = client or CalendarClient()
        self.job = None
        self.events = []
        self.loaded_day = None
        self.view = "today"
        self.render_key = None
        self.checked_day = datetime.now().astimezone().date()
        self.setObjectName("card")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SetMinimumSize)
        layout.setContentsMargins(18, 16, 18, 16)
        self.heading = text_label("Google Calendar")
        self.heading.setObjectName("serviceTitle")
        layout.addWidget(self.heading)
        self.status = text_label("기본 캘린더 · PC 현지 시간")
        self.status.setObjectName("caption")
        layout.addWidget(self.status)
        self.range_select = QComboBox()
        self.range_select.setAccessibleName("일정 조회 기간")
        for name, value in (("오늘", "today"), ("내일", "tomorrow"), ("주간 · 오늘부터 7일", "week")):
            self.range_select.addItem(name, value)
        self.range_select.currentIndexChanged.connect(self.change_range)
        layout.addWidget(self.range_select)
        quick_row = QHBoxLayout()
        self.quick_input = QLineEdit()
        self.quick_input.setMaxLength(400)
        self.quick_input.setPlaceholderText("내일 오후 3시 회의")
        self.quick_input.setAccessibleName("빠른 일정 입력")
        self.quick_input.setToolTip("오늘·내일·모레·YYYY-MM-DD + 시간 + 제목\n기본 1시간. 예: 내일 15:00 30분 동안 회의 / 내일 종일 출장")
        self.quick_input.returnPressed.connect(self.quick_event)
        self.quick_button = QPushButton("미리보기")
        self.quick_button.clicked.connect(self.quick_event)
        quick_row.addWidget(self.quick_input, 1)
        quick_row.addWidget(self.quick_button)
        layout.addLayout(quick_row)
        self.listing = text_label("Google 연결 후 오늘 일정을 표시합니다.")
        self.listing.setObjectName("calendarListing")
        layout.addWidget(self.listing)
        self.event_cards = QWidget()
        self.event_layout = QVBoxLayout(self.event_cards)
        self.event_layout.setContentsMargins(0, 0, 0, 0)
        self.event_layout.setSpacing(8)
        layout.addWidget(self.event_cards)
        self.success = text_label()
        self.success.setObjectName("success")
        self.success.hide()
        layout.addWidget(self.success)
        self.success_timer = QTimer(self)
        self.success_timer.setSingleShot(True)
        self.success_timer.timeout.connect(self.success.hide)
        row = QHBoxLayout()
        self.add_button = QPushButton("+ 일정")
        self.add_button.setObjectName("calendarAdd")
        self.add_button.setMinimumHeight(36)
        self.add_button.clicked.connect(self.add_event)
        self.refresh_button = QPushButton("갱신")
        self.refresh_button.clicked.connect(self.refresh)
        for button in (self.add_button, self.refresh_button):
            row.addWidget(button)
        layout.addLayout(row)
        links = QHBoxLayout()
        self.open_button = QPushButton("캘린더 열기")
        self.open_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://calendar.google.com/")))
        links.addWidget(self.open_button)
        self.settings_button = QToolButton()
        self.settings_button.setText("연결 설정")
        self.settings_button.setPopupMode(QToolButton.InstantPopup)
        settings_menu = QMenu(self.settings_button)
        self.connect_action = settings_menu.addAction("Google 연결", self.connect_google)
        self.disconnect_action = settings_menu.addAction("연결 해제", self.disconnect)
        self.settings_button.setMenu(settings_menu)
        links.addWidget(self.settings_button)
        layout.addLayout(links)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(300_000)
        self.day_timer = QTimer(self)
        self.day_timer.timeout.connect(self.tick)
        self.day_timer.start(10_000)
        self.update_controls()

    def update_controls(self):
        connected = self.client.connected()
        self.connect_action.setText("다시 연결" if connected else "Google 연결")
        self.connect_action.setEnabled(self.job is None)
        self.disconnect_action.setEnabled(connected and self.job is None)
        self.event_cards.setEnabled(self.job is None)
        self.quick_button.setEnabled(connected and self.job is None)
        self.quick_input.setEnabled(connected and self.job is None)
        for button in (self.add_button, self.refresh_button):
            button.setEnabled(connected and self.job is None)

    def run(self, action, callback, read_only=False):
        if self.job:
            return
        self.job = CalendarJob(action, self)
        # Closing waits for read-only jobs; login and writes block closing instead.
        self.job.read_only = read_only
        self.activity.emit(7)
        self.update_controls()

        def done():
            job = self.job
            self.job = None
            self.update_controls()
            self.activity.emit(5 if job.error else 8)
            callback(job.value, job.error)
            job.deleteLater()

        self.job.finished.connect(done)
        self.job.start()

    def refresh(self):
        if self.job or not self.client.connected():
            return
        self.status.setText("주간 일정 조회 중…")
        self.run(lambda: self.client.today(days=7), self.received, read_only=True)

    def received(self, snapshot, error):
        if error:
            self.status.setText(error + (" · 마지막 조회 결과" if self.loaded_day else ""))
            return
        self.loaded_day = snapshot["day"]
        self.events = snapshot["events"]
        self.status.setText(datetime.now().strftime("%H:%M 갱신") + " · 기본 캘린더 · 5분 간격")
        self.render()

    def events_in(self, view, now):
        """Loaded events overlapping the view's local days; the dashboard reuses "today"."""
        first = now.date() + timedelta(days=1 if view == "tomorrow" else 0)
        last = first + timedelta(days=7 if view == "week" else 1)
        visible = []
        for event in self.events:
            start, end = event["start"], event["end"]
            if "date" in start:
                a, b = date.fromisoformat(start["date"]), date.fromisoformat(end["date"])
                matches = a < last and b > first
            else:
                a = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00")).astimezone()
                b = datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00")).astimezone()
                matches = a.date() < last and (b - timedelta(microseconds=1)).date() >= first
            if matches:
                visible.append(event)
        return visible

    def render(self):
        key =(self.view, self.loaded_day, repr(self.events), datetime.now().strftime("%Y-%m-%d %H:%M"))
        if key == self.render_key:
            return
        self.render_key = key
        while self.event_layout.count():
            old = self.event_layout.takeAt(0).widget()
            old.hide()
            old.deleteLater()
        if self.loaded_day != datetime.now().astimezone().date():
            self.listing.setText("오늘 일정 갱신 필요")
            self.listing.show()
            return
        now = datetime.now().astimezone()
        visible = self.events_in(self.view, now)
        future = [e for e in visible if "dateTime" in e["start"]
                  and datetime.fromisoformat(e["start"]["dateTime"].replace("Z", "+00:00")) > now]
        next_event = min(future, key=lambda e: datetime.fromisoformat(e["start"]["dateTime"].replace("Z", "+00:00"))) if future else None
        self.listing.setText("\n\n".join(event_text(e) for e in visible) or "등록된 일정 없음. + 일정으로 추가하세요.")
        self.listing.setVisible(not visible)
        for event in visible:
            card = QWidget()
            card.setObjectName("eventCard")
            card.setAttribute(Qt.WA_StyledBackground, True)
            box = QVBoxLayout(card)
            box.setContentsMargins(12, 12, 12, 12)
            title = event.get("summary") or "(제목 없음)"
            description = event_text(dict(event, summary=""), now).removesuffix("(제목 없음)").rstrip(" ·")
            if self.view == "week":
                raw = event["start"].get("date") or datetime.fromisoformat(event["start"]["dateTime"].replace("Z", "+00:00")).astimezone().date().isoformat()
                description = raw[5:].replace("-", "/") + " · " + description
            active = "dateTime" in event["start"] and datetime.fromisoformat(event["start"]["dateTime"].replace("Z", "+00:00")) <= now < datetime.fromisoformat(event["end"]["dateTime"].replace("Z", "+00:00"))
            card.setProperty("current", active or event is next_event)
            timing = text_label(("다음 일정 · " if event is next_event else "") + description)
            timing.setObjectName("eventTime")
            box.addWidget(timing)
            box.addWidget(text_label(title))
            edit = QPushButton("일정 열기")
            edit.setAccessibleName(title + " 수정·삭제")
            edit.setEnabled(bool(event.get("id") and event.get("etag")))
            edit.clicked.connect(lambda checked=False, item=event: self.edit_event(item))
            box.addWidget(edit)
            self.event_layout.addWidget(card)

    def change_range(self):
        self.view = self.range_select.currentData()
        self.render()
        if not self.loaded_day:
            self.refresh()

    def show_success(self, text):
        self.success.setText(text)
        self.success.show()
        self.success_timer.start(4000)

    def edit_event(self, event):
        if not self.job:
            dialog = EventDialog(self, event)
            dialog.exec()
            dialog.deleteLater()

    def tick(self):
        today = datetime.now().astimezone().date()
        if today != self.checked_day:
            self.render()
            # A running job may hold the previous day; keep retrying until refresh can start.
            if not self.job:
                self.checked_day = today
                self.refresh()
        elif self.loaded_day:
            self.render()
        owner = self.window()
        if (self.client.connected() and self.loaded_day == today and hasattr(owner, "notifications")):
            owner.notifications.calendar(self.events)

    def connect_google(self):
        if self.client.connected():
            self.begin_connect(None)
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Google Calendar 최초 연결")
        dialog.resize(430, 370)
        layout = QVBoxLayout(dialog)
        layout.addWidget(text_label(
            "1. Google Cloud에서 프로젝트 생성·선택 후 Google Calendar API를 사용 설정하세요.\n\n"
            "2. Google Auth Platform에서 앱 이름·이메일을 설정하세요. 개인 계정은 외부 사용자로 설정하고, "
            "테스트 중이면 본인 Google 이메일을 테스트 사용자에 추가하세요.\n\n"
            "3. 클라이언트 만들기 → 데스크톱 앱 → JSON 다운로드.\n\n"
            "4. 아래에서 JSON을 선택하면 브라우저가 열립니다. 일정 접근을 허용하세요.\n\n"
            "로그인 정보는 이 PC에 저장. Google Tasks의 체크리스트는 포함하지 않습니다."))
        cloud = QPushButton("Google Cloud 설정 열기")
        cloud.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(
            "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com")))
        layout.addWidget(cloud)
        docs = QPushButton("공식 설정 안내 열기")
        docs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(
            "https://developers.google.com/workspace/calendar/api/quickstart/python?hl=ko")))
        layout.addWidget(docs)
        choose = QPushButton("OAuth JSON 선택하고 연결")

        def selected():
            path, _ = QFileDialog.getOpenFileName(dialog, "데스크톱 앱 OAuth JSON 선택", "", "JSON (*.json)")
            if path:
                dialog.accept()
                self.begin_connect(path)

        choose.clicked.connect(selected)
        layout.addWidget(choose)
        dialog.exec()
        dialog.deleteLater()

    def begin_connect(self, path):
        self.status.setText("브라우저에서 Google 로그인·권한 허용. 대기 제한 3분.")
        self.run(lambda: self.client.connect(path), self.connected)

    def connected(self, value, error):
        if error:
            self.status.setText(error)
        else:
            self.refresh()

    def add_event(self):
        dialog = EventDialog(self)
        dialog.exec()
        dialog.deleteLater()

    def quick_event(self):
        if self.job or not self.client.connected():
            return
        try:
            body = parse_quick_event(self.quick_input.text())
        except CalendarError as error:
            self.status.setText(str(error))
            return
        dialog = EventDialog(self)
        dialog.title_input.setText(body["summary"])
        all_day = "date" in body["start"]
        dialog.all_day.setChecked(all_day)
        for field, key in ((dialog.start, "start"), (dialog.end, "end")):
            raw = body[key]["date" if all_day else "dateTime"]
            value = QDateTime.fromString(raw, Qt.ISODate)
            field.setDateTime(value.addDays(-1) if all_day and key == "end" else value.toLocalTime())
        dialog.status.setText("날짜·시간을 확인한 뒤 일정 추가를 눌러 주세요. 길이 생략 시 1시간입니다.")
        if dialog.exec() == QDialog.Accepted:
            self.quick_input.clear()
        dialog.deleteLater()

    def disconnect(self):
        try:
            self.client.disconnect()
        except OSError:
            self.status.setText("연결 정보 삭제 실패. 폴더 권한 확인 필요.")
            return
        self.events = []
        self.loaded_day = None
        self.render_key = None
        self.render()
        self.listing.setText("Google 연결 후 오늘 일정을 표시합니다.")
        self.status.setText("PC 연결 정보 삭제됨. Google 계정의 앱 접근 권한은 계정 설정에서 해제 가능.")
        self.update_controls()
