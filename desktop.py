"""Desktop workspace reusing the widget's accounts, calendar and pet lifecycle."""
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QTimer, QRect, QRectF
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QShortcut, QKeySequence
from PySide6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QGridLayout,
                               QPushButton, QToolButton, QButtonGroup, QScrollArea, QSizePolicy)

from widget import Widget, SERVICES, THEMES, label
from billing import next_payment, payment_summary, PERIODS


PAGES = {
    "home": ("대시보드", "오늘 한눈에", "AI 사용량부터 일정까지, 오늘 필요한 정보를 모았습니다."),
    "quota": ("AI 한도", "AI 사용량", "주간 한도를 먼저 표시합니다. 계정별 상세 한도도 확인하세요."),
    "calendar": ("캘린더", "나의 일정", "예정된 일을 확인하고 빠르게 새 일정을 추가하세요."),
    "agents": ("에이전트", "진행 중인 작업", "승인 대기와 작업 상태를 확인하고 해당 창으로 이동하세요."),
    "billing": ("구독·결제", "구독 관리", "계정별 다음 결제 예정일을 한곳에서 관리하세요."),
}


def navigation_icon(kind, color):
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(2, 2)
    painter.setPen(QPen(QColor(color), 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    if kind == "home":
        for line in ((3, 10, 12, 3), (12, 3, 21, 10), (5, 9, 5, 21), (19, 9, 19, 21),
                     (5, 21, 10, 21), (14, 21, 19, 21), (10, 21, 10, 14), (10, 14, 14, 14), (14, 14, 14, 21)):
            painter.drawLine(*line)
    elif kind == "quota":
        for x, top in ((4, 13), (10, 8), (16, 3)):
            painter.drawRoundedRect(QRectF(x, top, 4, 18-top), 1, 1)
        painter.drawLine(3, 22, 22, 22)
    elif kind == "calendar":
        painter.drawRoundedRect(QRectF(3, 5, 18, 16), 3, 3)
        for line in ((3, 10, 21, 10), (8, 3, 8, 7), (16, 3, 16, 7), (8, 15, 11, 15), (14, 15, 17, 15)):
            painter.drawLine(*line)
    elif kind == "agents":
        painter.drawRoundedRect(QRectF(3, 6, 18, 14), 4, 4)
        for line in ((12, 2, 12, 6), (7, 11, 7, 13), (17, 11, 17, 13), (9, 17, 15, 17)):
            painter.drawLine(*line)
    else:
        painter.drawRoundedRect(QRectF(2, 5, 20, 15), 3, 3)
        painter.drawLine(3, 10, 21, 10)
        painter.drawLine(6, 16, 10, 16)
    painter.end()
    return QIcon(pixmap)


def panel(title, subtitle=""):
    box = QFrame()
    box.setObjectName("desktopPanel")
    layout = QVBoxLayout(box)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(12)
    layout.addWidget(label(title, "panelTitle"))
    if subtitle:
        layout.addWidget(label(subtitle, "caption"))
    return box, layout


def scroll_page(content):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.viewport().setAutoFillBackground(False)
    area.setWidget(content)
    return area


class DesktopWindow(Widget):
    def __init__(self, smoke=False):
        self.settings_prefix = "desktop/"
        self.desktop_ready = False
        super().__init__(smoke)
        self.setWindowTitle("check AI · 나의 작업 공간")
        self.setWindowIcon(QIcon(self.restore_button.grab()))
        self.setWindowFlags(Qt.Window)
        # Keep the alpha surface created by Widget; X11 cannot add it after show().
        self.setMinimumSize(820, 620)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.surface.setGraphicsEffect(None)
        self.retired_controls = QWidget(self)
        self.retired_controls.hide()
        theme_segment = self.theme_buttons["light"].parentWidget()
        keep = (self.pages, self.clock, self.page_heading, theme_segment, self.note)
        shell = self.surface.layout()

        def clear_layout(layout):
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().hide()
                    if item.widget() not in keep:
                        item.widget().setParent(self.retired_controls)
                elif item.layout():
                    clear_layout(item.layout())
        clear_layout(shell)
        shell.setContentsMargins(0, 0, 0, 0)
        self.pages.removeWidget(self.home_page)
        self.home_page.setParent(self.retired_controls)
        self.home_page.hide()

        body = QHBoxLayout()
        body.setSpacing(0)
        shell.addLayout(body)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(182)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(16, 28, 16, 18)
        sidebar_layout.setSpacing(8)
        brand = label("AI Desk", "desktopBrand")
        sidebar_layout.addWidget(brand)
        sidebar_layout.addWidget(label("나의 작업 공간", "caption"))
        sidebar_layout.addSpacing(30)
        self.nav_buttons = {}
        nav_group = QButtonGroup(self)
        for number, (key, info) in enumerate(PAGES.items(), 1):
            button = QPushButton(info[0])
            button.setObjectName("sidebarButton")
            button.setCheckable(True)
            button.setMinimumHeight(44)
            button.setIconSize(QSize(20, 20))
            button.setAccessibleName(info[0] + " 화면")
            button.setToolTip(info[0] + f" · Ctrl+{number}")
            button.clicked.connect(lambda checked=False, page=key: self.set_page(page, animate=True))
            nav_group.addButton(button)
            sidebar_layout.addWidget(button)
            self.nav_buttons[key] = button
            shortcut = QShortcut(QKeySequence(f"Ctrl+{number}"), self)
            shortcut.activated.connect(lambda page=key: self.set_page(page))
        sidebar_layout.addStretch()
        github = QPushButton("GitHub 열기")
        github.setObjectName("sidebarButton")
        github.setIcon(QIcon(self.page_buttons["github"].grab(QRect(18, 8, 64, 64))))
        github.setIconSize(QSize(20, 20))
        github.setMinimumHeight(42)
        github.clicked.connect(lambda: self.open_web_app("github"))
        sidebar_layout.addWidget(github)
        self.settings_button = QToolButton()
        self.settings_button.setText("설정")
        self.settings_button.setObjectName("sidebarSettings")
        self.settings_button.setPopupMode(QToolButton.InstantPopup)
        self.settings_button.setMinimumHeight(40)
        self.settings_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.menu.setParent(self.settings_button, Qt.Popup)
        for action in self.menu.actions():
            if action.text() == "위젯 크기":
                self.menu.removeAction(action)
        self.settings_button.setMenu(self.menu)
        sidebar_layout.addWidget(self.settings_button)
        body.addWidget(sidebar)

        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(26, 22, 26, 16)
        main_layout.setSpacing(16)
        top = QHBoxLayout()
        top.addWidget(self.clock, 1)
        top.addWidget(theme_segment)
        pet = QPushButton("펫 모드")
        pet.setAccessibleName("작은 펫으로 접기")
        pet.clicked.connect(self.toggle_collapsed)
        top.addWidget(pet)
        main_layout.addLayout(top)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(3)
        heading.addWidget(self.page_heading)
        self.page_description = label("", "desktopDescription")
        heading.addWidget(self.page_description)
        header.addLayout(heading, 1)
        self.quick_add = QPushButton("+ 일정 추가")
        self.quick_add.setObjectName("desktopPrimary")
        self.quick_add.setMinimumHeight(38)
        self.quick_add.clicked.connect(self.add_calendar_event)
        header.addWidget(self.quick_add, 0, Qt.AlignTop)
        main_layout.addLayout(header)
        main_layout.addWidget(self.pages, 1)
        footer = QHBoxLayout()
        footer.addWidget(self.note, 1)
        footer.addWidget(label("주간 한도 우선 · 3분마다 갱신", "caption"))
        main_layout.addLayout(footer)
        body.addWidget(main, 1)
        for item in keep:
            if item is not self.note:
                item.show()

        # Move the existing live cards into a responsive grid; no duplicate fetches.
        old_content = self.scroll.takeWidget()
        self.quota_content = QWidget()
        self.quota_grid = QGridLayout(self.quota_content)
        self.quota_grid.setContentsMargins(0, 0, 4, 0)
        self.quota_grid.setSpacing(14)
        self.quota_grid.setAlignment(Qt.AlignTop)
        for card in self.cards.values():
            card.setParent(self.quota_content)
            card.show()
        self.scroll.setWidget(self.quota_content)
        old_content.deleteLater()
        self.quota_columns = 0
        self.build_dashboard()
        self.build_agents()
        self.build_billing()
        self.page_indices = {"quota": self.pages.indexOf(self.quota_page),
                             "calendar": self.pages.indexOf(self.calendar_scroll),
                             "home": self.pages.indexOf(self.dashboard_page),
                             "agents": self.pages.indexOf(self.agents_page),
                             "billing": self.pages.indexOf(self.billing_page)}
        self.desktop_ready = True
        self.current_page = "home"
        self.set_theme(self.theme)
        self.resize(1160, 800)
        geometry = None if smoke else self.settings.value("desktop/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.move(self.screen().availableGeometry().center() - self.rect().center())
        self.set_page("home")
        self.reflow_cards()
        self.update_dashboard()
        self.calendar.activity.connect(lambda _: QTimer.singleShot(0, self.update_dashboard))
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self.refresh)

    def build_dashboard(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(16)
        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.metrics = {}
        for key, title in (("accounts", "AI 계정"), ("agents", "진행 중인 작업"), ("events", "오늘 일정"), ("billing", "가장 가까운 결제")):
            box, inner = panel(title)
            value = label("—", "metricValue")
            inner.addWidget(value)
            self.metrics[key] = value
            metrics.addWidget(box, 1)
        layout.addLayout(metrics)
        grid = QGridLayout()
        grid.setSpacing(16)
        quotas, quota_layout = panel("AI 한도 요약", "주간 잔여량을 먼저 확인하세요")
        self.overview_quotas = {}
        for key, info in SERVICES.items():
            row = QHBoxLayout()
            row.addWidget(label(info[0], "overviewName"), 1)
            value = label("조회 전", "overviewValue")
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(value)
            self.overview_quotas[key] = value
            quota_layout.addLayout(row)
        quota_layout.addStretch()
        details = QPushButton("모든 한도 보기  →")
        details.setObjectName("desktopTextButton")
        details.clicked.connect(lambda: self.set_page("quota"))
        quota_layout.addWidget(details, 0, Qt.AlignLeft)
        schedule, schedule_layout = panel("다가오는 일정", "현재 조회한 캘린더 기준")
        self.overview_schedule = label("캘린더 연결 후 일정을 표시합니다.", "scheduleSummary")
        schedule_layout.addWidget(self.overview_schedule, 1)
        open_calendar = QPushButton("캘린더 열기  →")
        open_calendar.setObjectName("desktopTextButton")
        open_calendar.clicked.connect(lambda: self.set_page("calendar"))
        schedule_layout.addWidget(open_calendar, 0, Qt.AlignLeft)
        grid.addWidget(quotas, 0, 0)
        grid.addWidget(schedule, 0, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        activity, activity_layout = panel("에이전트 작업", "작업이 감지되면 여기에 표시됩니다")
        self.overview_activity = label("실행 중인 작업이 없습니다.", "scheduleSummary")
        activity_layout.addWidget(self.overview_activity)
        open_agents = QPushButton("작업 목록 열기  →")
        open_agents.setObjectName("desktopTextButton")
        open_agents.clicked.connect(lambda: self.set_page("agents"))
        activity_layout.addWidget(open_agents, 0, Qt.AlignLeft)
        layout.addWidget(activity)
        layout.addStretch()
        self.dashboard_page = scroll_page(content)
        self.pages.addWidget(self.dashboard_page)

    def build_agents(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 4, 0)
        box, inner = panel("실시간 작업", "Codex · Claude Code · Antigravity · GitHub Copilot")
        self.agents_empty = label("감지된 작업이 없습니다.\n이 PC의 터미널이나 에이전트 앱에서 작업을 시작하면 표시됩니다.", "scheduleSummary")
        inner.addWidget(self.agents_empty)
        self.task_layout = QVBoxLayout()
        self.task_layout.setSpacing(10)
        inner.addLayout(self.task_layout)
        layout.addWidget(box)
        layout.addWidget(label("작업을 누르면 해당 터미널 또는 앱으로 이동합니다. 완료·실패 알림은 기존 설정을 따릅니다.", "caption"))
        layout.addStretch()
        self.task_buttons = []
        self.task_signature = None
        self.agents_page = scroll_page(content)
        self.pages.addWidget(self.agents_page)

    def build_billing(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(12)
        self.billing_labels = {}
        self.billing_plans = {}
        for key, info in SERVICES.items():
            box, inner = panel(info[0])
            self.billing_plans[key] = label("플랜 확인 중", "caption")
            inner.addWidget(self.billing_plans[key])
            row = QHBoxLayout()
            value = label("다음 결제일 미설정", "billingValue")
            row.addWidget(value, 1)
            edit = QPushButton("결제일 설정")
            edit.setAccessibleName(info[0] + " 결제일 설정")
            edit.clicked.connect(lambda checked=False, service=key: self.edit_billing(service))
            row.addWidget(edit)
            inner.addLayout(row)
            self.billing_labels[key] = value
            layout.addWidget(box)
        layout.addWidget(label("결제일은 직접 설정한 예정일입니다. 한도 초기화 시각과 별도로 관리합니다.", "caption"))
        layout.addStretch()
        self.billing_page = scroll_page(content)
        self.pages.addWidget(self.billing_page)

    def add_calendar_event(self):
        self.set_page("calendar")
        if self.calendar.client.connected():
            self.calendar.add_event()
        else:
            self.calendar.settings_button.setFocus()
            self.calendar.status.setText("연결 설정에서 Google 계정을 연결하면 일정을 추가할 수 있습니다.")

    def set_page(self, page, animate=False):
        if not self.desktop_ready:
            return super().set_page(page, animate)
        index = self.page_indices[page]
        changed = self.pages.currentIndex() != index
        if changed and animate and not self.reduce_motion.isChecked():
            self.page_opacity.snapshot = self.pages.grab()
            self.page_opacity.setGeometry(self.pages.rect())
        self.pages.setCurrentIndex(index)
        self.current_page = page
        self.nav_buttons[page].setChecked(True)
        self.update_navigation_icons()
        self.page_heading.setText(PAGES[page][1])
        self.page_heading.show()
        self.page_description.setText(PAGES[page][2])
        self.home_button.hide()
        if changed:
            self.page_motion.stop()
            if animate and not self.reduce_motion.isChecked():
                self.page_opacity.raise_()
                self.page_motion.setStartValue(0.0)
                self.page_motion.setEndValue(1.0)
                self.page_motion.start()
            else:
                self.page_opacity.setOpacity(1.0)
        self.reflow_cards()
        self.update_dashboard()

    def reflow_cards(self):
        if not self.desktop_ready or self.collapsed:
            return
        columns = 2 if self.width() >= 1060 else 1
        if columns == self.quota_columns:
            return
        self.quota_columns = columns
        for card in self.cards.values():
            self.quota_grid.removeWidget(card)
        for index, card in enumerate(self.cards.values()):
            self.quota_grid.addWidget(card, index // columns, index % columns)
        self.quota_grid.setColumnStretch(0, 1)
        self.quota_grid.setColumnStretch(1, 1 if columns == 2 else 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.desktop_ready:
            self.reflow_cards()
            self.page_opacity.setGeometry(self.pages.rect())

    def received(self, key, snapshot, error):
        super().received(key, snapshot, error)
        if self.desktop_ready:
            self.update_dashboard()

    def received_activity(self, activity):
        super().received_activity(activity)
        if self.desktop_ready:
            self.update_dashboard()

    def update_billing(self):
        super().update_billing()
        if self.desktop_ready:
            self.update_dashboard()

    def update_clock(self):
        super().update_clock()
        if self.desktop_ready:
            minute = datetime.now().strftime("%Y-%m-%d %H:%M")
            if getattr(self, "overview_minute", None) != minute:
                self.overview_minute = minute
                self.update_dashboard()

    def update_dashboard(self):
        if not self.desktop_ready:
            return
        self.metrics["accounts"].setText(f"{sum(bool(c.data) for c in self.cards.values())} / {len(self.cards)}")
        for key, card in self.cards.items():
            value = self.pet_quotas.get(key, "조회 전")
            if value == "조회 실패" and card.data:
                value = card.amount.text() + " · 조회 실패"
            self.overview_quotas[key].setText(value)
            self.overview_quotas[key].setToolTip(card.summary_label.text() + ("\n" + card.status.text() if not card.status.isHidden() else ""))
            record = self.billing_record(key)
            self.billing_labels[key].setText(payment_summary(record) + (" · " + PERIODS[record["period"]] if record else ""))
            self.billing_plans[key].setText(card.plan.text())
        self.metrics["agents"].setText(str(len(self.agent_activity)))
        today = datetime.now().astimezone().date()
        dates = [next_payment(self.billing_record(key), today) for key in SERVICES]
        upcoming = [day for day in dates if day and day >= today]
        self.metrics["billing"].setText(min(upcoming).strftime("%m.%d") if upcoming else "확인 필요" if any(dates) else "미설정")
        self.render_schedule()
        self.render_agents()

    def render_schedule(self):
        now = datetime.now().astimezone()
        if self.calendar.loaded_day != now.date():
            self.metrics["events"].setText("—")
            self.overview_schedule.setText("캘린더 연결 또는 갱신이 필요합니다.\n캘린더에서 오늘의 일정을 확인하세요.")
            return
        lines = []
        for event in self.calendar.events:
            start = event.get("start", {})
            title = str(event.get("summary") or "제목 없음")
            try:
                if "dateTime" in start:
                    begin = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00")).astimezone()
                    end = datetime.fromisoformat(event.get("end", {}).get("dateTime", start["dateTime"]).replace("Z", "+00:00")).astimezone()
                    if end >= now:
                        lines.append((begin.isoformat(), f"{begin:%m/%d %H:%M}   {title[:70]}"))
                else:
                    day = start["date"]
                    finish = event.get("end", {}).get("date", day)
                    if finish > now.date().isoformat():
                        lines.append((day, f"{day[5:].replace('-', '/')} · 종일   {title[:70]}"))
            except (KeyError, TypeError, ValueError):
                continue
        # Same filter as the calendar's 오늘 view, whichever range the calendar shows.
        self.metrics["events"].setText(str(len(self.calendar.events_in("today", now))))
        self.overview_schedule.setText("\n\n".join(item[1] for item in sorted(lines)[:4]) or "다가오는 일정이 없습니다.")

    def render_agents(self):
        signature = tuple((task["id"], task["status"], task["label"], task.get("elapsed", 0)//60) for task in self.agent_activity)
        if signature == self.task_signature:
            return
        self.task_signature = signature
        while self.task_layout.count():
            self.task_layout.takeAt(0).widget().deleteLater()
        self.task_buttons = []
        summaries = []
        for index, task in enumerate(self.agent_activity):
            title = SERVICES[task["agent"]][0]
            elapsed = task.get("elapsed", 0)
            duration = f"{elapsed//60}분 경과" if elapsed >= 60 else "방금 시작"
            status = "승인·입력 대기" if task["status"] == "waiting" else "작업 중"
            button = QPushButton(f"{title} · {status} · {duration}\n{task['label']}")
            button.setObjectName("agentTask")
            button.setProperty("waiting", task["status"] == "waiting")
            button.setMinimumHeight(76)
            button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            button.setAccessibleName(title + " · " + status + " · " + task["label"])
            asset = Path(__file__).parent / "assets" / (task["agent"] + (".png" if task["agent"] == "antigravity" else ".svg"))
            button.setIcon(QIcon(str(asset)))
            button.setIconSize(QSize(28, 28))
            button.setToolTip(task["label"] + "\n클릭하여 작업 창으로 이동")
            button.clicked.connect(lambda checked=False, item=index: self.open_activity(str(item)))
            self.task_layout.addWidget(button)
            self.task_buttons.append(button)
            summaries.append(f"{title}   ·   {status}   ·   {duration}\n{task['label']}")
        self.agents_empty.setVisible(not self.task_buttons)
        self.overview_activity.setText("\n\n".join(summaries[:3]) or "실행 중인 작업이 없습니다.")

    def toggle_collapsed(self):
        if not self.desktop_ready:
            return super().toggle_collapsed()
        was_collapsed = self.collapsed
        if not was_collapsed:
            self.desktop_maximized = self.isMaximized()
            if self.desktop_maximized:
                maximized_geometry = self.saveGeometry()
                self.showNormal()
        super().toggle_collapsed()
        if not was_collapsed and self.desktop_maximized:
            # closeEvent saves expanded_geometry in pet mode; keep the maximized state for next launch.
            self.expanded_geometry = maximized_geometry
        if was_collapsed:
            self.setWindowFlags(Qt.Window)
            self.setMinimumSize(820, 620)
            self.layout().setContentsMargins(0, 0, 0, 0)
            if self.desktop_maximized:
                self.showMaximized()
            else:
                self.show()
            self.reflow_cards()
        else:
            self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
            self.setAttribute(Qt.WA_QuitOnClose, True)
            self.show()
        self.raise_()
        self.activateWindow()

    def bring_to_front(self):
        if self.collapsed:
            self.toggle_collapsed()
        if self.isMaximized():
            self.showMaximized()
            self.raise_()
            self.activateWindow()
        else:
            super().bring_to_front()

    def set_theme(self, theme):
        super().set_theme(theme)
        if not self.desktop_ready:
            return
        c = THEMES[theme]
        canvas = "#f5f6f8" if theme == "light" else "#171a21"
        sidebar = "#eef0f4" if theme == "light" else "#1e222c"
        selected = "#dfeaff" if theme == "light" else "#2b3b55"
        accent = "#3268ce" if theme == "light" else "#9bbcff"
        self.setStyleSheet(self.styleSheet() + f"""
            QWidget#window {{background:{canvas};}}
            QFrame#surface {{background:{canvas}; border:0; border-radius:0;}}
            QFrame#sidebar {{background:{sidebar}; border:0; border-right:1px solid {c['border']};}}
            QLabel#desktopBrand {{font-size:26px; font-weight:750; letter-spacing:-1px;}}
            QLabel#clock {{font-size:14px; color:{c['muted']}; font-weight:500;}}
            QLabel#pageHeading {{font-size:28px; padding:0;}}
            QLabel#desktopDescription {{font-size:12px; color:{c['muted']};}}
            QPushButton#sidebarButton {{text-align:left; padding:10px 12px; background:transparent; border:0; border-radius:10px; font-size:13px;}}
            QPushButton#sidebarButton:hover {{background:{c['control']};}}
            QPushButton#sidebarButton:checked {{background:{selected}; color:{accent}; font-weight:650;}}
            QToolButton#sidebarSettings {{text-align:left; padding:8px 14px; background:transparent; border:1px solid {c['border']};}}
            QFrame#desktopPanel {{background:{c['card']}; border:1px solid {c['border']}; border-radius:16px;}}
            QLabel#panelTitle {{font-size:13px; font-weight:650;}}
            QLabel#metricValue {{font-size:27px; font-weight:700; color:{accent};}}
            QLabel#overviewName {{font-size:12px; padding:5px 0;}}
            QLabel#overviewValue {{font-size:12px; font-weight:650; color:{accent};}}
            QLabel#scheduleSummary {{font-size:13px; padding:4px 0;}}
            QPushButton#desktopTextButton {{background:transparent; color:{accent}; padding:5px 0; border:0; font-size:12px;}}
            QPushButton#desktopPrimary {{background:{accent}; color:{c['card']}; font-weight:650; padding:8px 16px;}}
            QPushButton#agentTask {{text-align:left; padding:14px; background:{canvas}; border:1px solid {c['border']}; border-radius:12px; font-size:13px;}}
            QPushButton#agentTask[waiting='true'] {{border-color:{c['warning']};}}
            QLabel#billingValue {{font-size:14px; font-weight:550;}}
            QWidget#card {{border-radius:16px;}}
        """)
        self.update_navigation_icons()

    def update_navigation_icons(self):
        accent = "#3268ce" if self.theme == "light" else "#9bbcff"
        for key, button in self.nav_buttons.items():
            button.setIcon(navigation_icon(key, accent if button.isChecked() else THEMES[self.theme]["muted"]))
