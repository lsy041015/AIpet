"""Desktop workspace reusing the widget's accounts, calendar and pet lifecycle."""
from datetime import datetime
from html import escape

from PySide6.QtCore import Qt, QSize, QTimer, QRect, QRectF, QPointF, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QShortcut, QKeySequence
from PySide6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QGridLayout,
                               QLabel, QPushButton, QToolButton, QButtonGroup, QScrollArea, QSizePolicy)

from widget import (Widget, SERVICES, THEMES, DISPLAY_CSS, label, risk_level, set_risk, account_title, nearest_reset, time_left,
                    service_info, session_title, model_text, subagent_text, todo_progress, theme_colors, rgba, neon_tube,
                    LogoBadge, NeonSign, GlowLabel, NeonBar, GlowFrame)
from billing import next_payment, price_summary, PERIODS


PAGES = {
    "home": ("대시보드", "오늘 한눈에", "AI 사용량부터 일정까지, 오늘 필요한 정보를 모았습니다."),
    "quota": ("AI 한도", "AI 사용량", "주간 한도를 먼저 표시합니다. 계정별 상세 한도도 확인하세요."),
    "calendar": ("캘린더", "나의 일정", "예정된 일을 확인하고 빠르게 새 일정을 추가하세요."),
    "todo": ("할 일", "할 일 목록", "해야 할 일을 적고 완료 여부를 관리하세요."),
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
    elif kind == "todo":
        for y in (6, 12, 18):
            painter.drawLine(3, y, 5, y + 2)
            painter.drawLine(5, y + 2, 8, y - 2)
            painter.drawLine(12, y, 21, y)
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


class NavButton(QPushButton):
    """Sidebar entry; the selected one gets a short neon tube on its left edge."""
    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.isChecked():
            return
        colors = theme_colors(self)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        tube = QPainterPath(QPointF(7, 13))
        tube.lineTo(7, self.height() - 13)
        if colors["glow"]:
            neon_tube(p, tube, QColor(colors["accent"]), 2.4)
        else:
            p.strokePath(tube, QPen(QColor(colors["accent"]), 3, Qt.SolidLine, Qt.RoundCap))


class SessionCard(GlowFrame):
    """One agent session: title, model chip, status, current todo with progress and running subagents."""
    activated = Signal()
    expanded_changed = Signal(str, bool)
    radius = 14

    def __init__(self, task, colors, expanded=False, compact=False):
        super().__init__()
        self.task_id = str(task.get("id"))
        waiting = task.get("status") == "waiting"
        agent, activity = task.get("agent"), str(task.get("label") or "")
        name, title = service_info(agent)[0], session_title(task)
        status = "승인·입력 대기" if waiting else "작업 중"
        elapsed = task.get("elapsed", 0)
        duration = f"{elapsed // 60}분 경과" if elapsed >= 60 else "방금 시작"
        self.setObjectName("sessionCard")
        self.setProperty("waiting", waiting)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Maximum)
        self.setAccessibleName(" · ".join(v for v in (title, name, status, duration, activity) if v))
        self.setToolTip(activity + "\n클릭하여 작업 창으로 이동")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(7)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(LogoBadge(agent, 34), 0, Qt.AlignTop)
        names = QVBoxLayout()
        names.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(label(title, "sessionTitle"), 1)
        pill = label("● " + status, "statusPill")
        pill.setWordWrap(False)
        pill.setProperty("waiting", waiting)
        top.addWidget(pill, 0, Qt.AlignTop)
        names.addLayout(top)
        meta = QHBoxLayout()
        meta.setSpacing(8)
        details = [name] + ([activity] if activity and activity != status else []) + [duration]
        meta.addWidget(label(" · ".join(details), "caption"), 1)
        if model_text(task):
            chip = label(model_text(task), "modelChip")
            chip.setWordWrap(False)
            chip.setToolTip("메인 모델 · effort")
            meta.addWidget(chip, 0, Qt.AlignVCenter)
        names.addLayout(meta)
        head.addLayout(names, 1)
        layout.addLayout(head)
        todos = [item for item in task.get("todos") or () if isinstance(item, dict)]
        done, total = todo_progress(task)
        current = task.get("current_task") or next((item.get("text") for item in todos if item.get("status") == "in_progress"), None)
        self.toggle = None
        if current or total:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(label("▸ " + (current or ("할 일 모두 완료" if done == total else "다음 할 일 대기")), "currentTask"), 1)
            if total and not compact:
                self.toggle = QToolButton()
                self.toggle.setObjectName("todoToggle")
                self.toggle.setCheckable(True)
                self.toggle.setAccessibleName(f"할 일 {done}/{total} 목록 펼치기")
                row.addWidget(self.toggle, 0, Qt.AlignVCenter)
            elif total:
                row.addWidget(label(f"할 일 {done}/{total}", "caption"), 0, Qt.AlignVCenter)
            layout.addLayout(row)
            if total:
                progress = NeonBar(3)
                progress.setObjectName("todoProgress")
                progress.setValue(round(done / total * 1000))
                progress.setAccessibleName(f"할 일 {done}/{total} 완료")
                layout.addWidget(progress)
        helpers = [item for item in task.get("subagents") or () if isinstance(item, dict)]
        if helpers:
            chips = QHBoxLayout()
            chips.setSpacing(6)
            shown = helpers[:2 if compact else 3]
            for helper in shown:
                running = helper.get("status", "running") == "running"
                chip = QLabel(f'<span style="color:{colors["accent"] if running else colors["muted"]};">●</span>&nbsp;'
                              + escape(subagent_text(helper)))
                chip.setObjectName("agentChip")
                chip.setProperty("running", running)
                chip.setToolTip(str(helper.get("description") or helper.get("type") or "서브에이전트"))
                chip.setAccessibleName("서브에이전트 " + subagent_text(helper) + (" 실행 중" if running else ""))
                chips.addWidget(chip)
            if len(helpers) > len(shown):
                chips.addWidget(label(f"+{len(helpers) - len(shown)}", "agentChip"))
            chips.addStretch(1)
            layout.addLayout(chips)
        self.todo_list = None
        if self.toggle is not None:
            marks = {"completed": ("✓", colors["muted"]), "in_progress": ("●", colors["accent"]), "cancelled": ("✕", colors["muted"])}
            rows = []
            for item in todos:
                mark, color = marks.get(item.get("status"), ("○", colors["text"]))
                text = escape(str(item.get("text") or ""))
                if item.get("status") in ("completed", "cancelled"):
                    text = f"<s>{text}</s>"
                rows.append(f'<span style="color:{color};">{mark}&nbsp;&nbsp;{text}</span>')
            self.todo_list = QLabel("<br>".join(rows))
            self.todo_list.setObjectName("todoList")
            self.todo_list.setWordWrap(True)
            self.todo_list.hide()
            layout.addWidget(self.todo_list)
            self.toggle.toggled.connect(self.show_todos)
            self.summary = f"할 일 {done}/{total}"
            self.show_todos(expanded)
            self.toggle.setChecked(expanded)

    def show_todos(self, expanded):
        self.todo_list.setVisible(expanded)
        self.toggle.setText(self.summary + (" ▴" if expanded else " ▾"))
        self.expanded_changed.emit(self.task_id, expanded)

    def set_expanded(self, expanded):
        if self.toggle is not None:
            self.toggle.setChecked(expanded)

    def lit(self):
        # Waiting sessions keep their warning edge lit until the agent continues.
        return super().lit() or bool(self.property("waiting"))

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.activated.emit()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.activated.emit()
        else:
            super().keyPressEvent(event)


def set_muted(item, muted):
    if item.property("muted") != muted:
        item.setProperty("muted", muted)
        item.style().unpolish(item)
        item.style().polish(item)


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


def due_text(upcoming, today):
    days = (upcoming - today).days
    return "오늘" if days == 0 else f"{days}일 후" if days > 0 else "확인 필요"


def place(grid, widgets, columns):
    for item in widgets:
        grid.removeWidget(item)
    for index, item in enumerate(widgets):
        grid.addWidget(item, index // columns, index % columns)
    grid.setColumnStretch(0, 1)
    grid.setColumnStretch(1, 1 if columns == 2 else 0)


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
        self.quota_view_switch.hide()
        self.set_quota_view("cards", persist=False)
        self.setWindowTitle("check AI · 나의 작업 공간")
        self.setWindowIcon(QIcon(self.restore_button.grab()))
        self.setWindowFlags(Qt.Window)
        # Keep the alpha surface created by Widget; X11 cannot add it after show().
        self.setMinimumSize(820, 620)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.shadow = None
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
        self.brand = NeonSign("check AI", 28, brand=True)
        self.neon_signs.append(self.brand)
        sidebar_layout.addWidget(self.brand)
        sidebar_layout.addWidget(label("나의 작업 공간", "caption"))
        sidebar_layout.addSpacing(30)
        self.nav_buttons = {}
        nav_group = QButtonGroup(self)
        for number, (key, info) in enumerate(PAGES.items(), 1):
            button = NavButton(info[0])
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
        self.add_tile.setParent(self.quota_content)
        self.add_tile.setMinimumHeight(120)
        self.add_tile.show()
        self.scroll.setWidget(self.quota_content)
        old_content.deleteLater()
        self.quota_columns = 0
        self.build_dashboard()
        self.build_agents()
        self.build_billing()
        self.page_indices = {"quota": self.pages.indexOf(self.quota_page),
                             "calendar": self.pages.indexOf(self.calendar_scroll),
                             "todo": self.pages.indexOf(self.todo_scroll),
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
        self.metrics, self.metric_notes = {}, {}
        for key, title in (("lowest", "가장 적게 남은 한도"), ("agents", "진행 중인 작업"), ("events", "오늘 일정"), ("billing", "가장 가까운 결제")):
            box, inner = panel(title)
            # A wrapped title in one box must not push its value out of line with the others.
            inner.addStretch()
            value = GlowLabel("—")
            value.setObjectName("metricValue")
            inner.addWidget(value)
            note = label("오늘 기준" if key == "events" else "", "metricNote")
            inner.addWidget(note)
            self.metrics[key], self.metric_notes[key] = value, note
            metrics.addWidget(box, 1)
        layout.addLayout(metrics)
        grid = QGridLayout()
        grid.setSpacing(16)
        quotas, quota_layout = panel("AI 한도 요약", "주간 잔여량을 먼저 확인하세요")
        self.overview_quotas, self.overview_bars, self.overview_rows = {}, {}, {}
        self.overview_layout = QVBoxLayout()
        self.overview_layout.setSpacing(2)
        quota_layout.addLayout(self.overview_layout)
        self.overview_empty = label("등록된 에이전트가 없습니다.\n에이전트를 추가하면 남은 한도를 표시합니다.", "scheduleSummary")
        quota_layout.addWidget(self.overview_empty)
        self.rebuild_overview()
        quota_layout.addStretch()
        links = QHBoxLayout()
        details = QPushButton("모든 한도 보기  →")
        details.setObjectName("desktopTextButton")
        details.clicked.connect(lambda: self.set_page("quota"))
        links.addWidget(details)
        links.addStretch()
        add = QPushButton("+ 에이전트 추가")
        add.setObjectName("desktopTextButton")
        add.setAccessibleName("에이전트 추가")
        add.clicked.connect(self.add_account)
        links.addWidget(add)
        quota_layout.addLayout(links)
        schedule, schedule_layout = panel("다가오는 일정", "현재 조회한 캘린더 기준")
        self.overview_schedule = label("캘린더 연결 후 일정을 표시합니다.", "scheduleSummary")
        self.overview_schedule.setAlignment(Qt.AlignLeft | Qt.AlignTop)
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
        self.overview_sessions = QVBoxLayout()
        self.overview_sessions.setSpacing(10)
        activity_layout.addLayout(self.overview_sessions)
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
        box, inner = panel("실시간 작업", "Claude Code · Codex · Antigravity · GitHub Copilot · opencode")
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
        self.expanded_sessions = set()
        self.agents_page = scroll_page(content)
        self.pages.addWidget(self.agents_page)

    def build_billing(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(14)
        box, inner = panel("결제 요약")
        self.billing_summary = label("", "billingSummary")
        inner.addWidget(self.billing_summary)
        layout.addWidget(box)
        self.billing_grid = QGridLayout()
        self.billing_grid.setSpacing(14)
        self.billing_cards, self.billing_plans, self.billing_dates = {}, {}, {}
        self.billing_notes, self.billing_buttons, self.billing_prices, self.billing_resets = {}, {}, {}, {}
        self.rebuild_billing_cards()
        layout.addLayout(self.billing_grid)
        layout.addWidget(label("결제일은 직접 설정한 예정일입니다. 한도 초기화 시각과 별도로 관리합니다.", "caption"))
        layout.addStretch()
        self.billing_columns = 0
        self.billing_page = scroll_page(content)
        self.pages.addWidget(self.billing_page)

    def rebuild_overview(self):
        for key in [key for key in self.overview_rows if key not in self.cards]:
            row = self.overview_rows.pop(key)
            row.setParent(None)
            row.deleteLater()
            del self.overview_quotas[key], self.overview_bars[key]
        for account in self.accounts:
            key = account["id"]
            if key in self.overview_rows:
                continue
            box = QWidget()
            stack = QVBoxLayout(box)
            stack.setContentsMargins(0, 0, 0, 0)
            stack.setSpacing(2)
            row = QHBoxLayout()
            row.setSpacing(10)
            row.addWidget(LogoBadge(account["kind"], 22))
            row.addWidget(label(account_title(account), "overviewName"), 1)
            value = label(self.pet_quotas.get(key, "조회 전"), "overviewValue")
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(value)
            stack.addLayout(row)
            bar = NeonBar(4)
            bar.setObjectName("overviewBar")
            bar.setProperty("service", account["kind"])
            policy = bar.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            bar.setSizePolicy(policy)
            stack.addWidget(bar)
            self.overview_layout.addWidget(box)
            self.overview_rows[key], self.overview_quotas[key], self.overview_bars[key] = box, value, bar
        self.overview_empty.setVisible(not self.accounts)

    def rebuild_billing_cards(self):
        for key in [key for key in self.billing_cards if key not in self.cards]:
            box = self.billing_cards.pop(key)
            box.setParent(None)
            box.deleteLater()
            for store in (self.billing_plans, self.billing_dates, self.billing_notes, self.billing_buttons,
                          self.billing_prices, self.billing_resets):
                del store[key]
        for account in self.accounts:
            key, title = account["id"], account_title(account)
            if key in self.billing_cards:
                continue
            box = QFrame()
            box.setObjectName("desktopPanel")
            stack = QVBoxLayout(box)
            stack.setContentsMargins(20, 18, 20, 18)
            stack.setSpacing(10)
            head = QHBoxLayout()
            head.setSpacing(12)
            logo = LogoBadge(account["kind"], 36)
            logo.setAccessibleName(title + " 로고")
            head.addWidget(logo)
            names = QVBoxLayout()
            names.setSpacing(2)
            names.addWidget(label(title, "serviceTitle"))
            self.billing_plans[key] = label("플랜 확인 중", "caption")
            names.addWidget(self.billing_plans[key])
            head.addLayout(names, 1)
            stack.addLayout(head)
            self.billing_dates[key] = GlowLabel("미설정")
            self.billing_dates[key].setObjectName("billingDate")
            stack.addWidget(self.billing_dates[key])
            self.billing_prices[key] = label("", "caption")
            stack.addWidget(self.billing_prices[key])
            self.billing_resets[key] = label("", "caption")
            stack.addWidget(self.billing_resets[key])
            foot = QHBoxLayout()
            self.billing_notes[key] = label("", "caption")
            foot.addWidget(self.billing_notes[key], 1)
            edit = QPushButton("결제일 설정")
            edit.setAccessibleName(title + " 결제일 설정")
            edit.clicked.connect(lambda checked=False, service=key: self.edit_billing(service))
            foot.addWidget(edit)
            stack.addLayout(foot)
            self.billing_cards[key], self.billing_buttons[key] = box, edit

    def layout_cards(self):
        if not self.desktop_ready:
            return super().layout_cards()
        self.insert_missing(self.list_layout, self.quota_rows)
        for card in self.cards.values():
            if card.parent() is not self.quota_content:
                card.setParent(self.quota_content)
                card.show()
        place(self.quota_grid, [*self.cards.values(), self.add_tile], self.quota_columns or 1)
        self.rebuild_overview()
        self.rebuild_billing_cards()
        place(self.billing_grid, list(self.billing_cards.values()), self.billing_columns or 1)

    def sync_accounts(self):
        super().sync_accounts()
        self.update_dashboard()

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
        self.quick_add.setVisible(page != "todo")
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
        if columns != self.quota_columns:
            self.quota_columns = columns
            place(self.quota_grid, [*self.cards.values(), self.add_tile], columns)
        if columns != self.billing_columns:
            self.billing_columns = columns
            place(self.billing_grid, list(self.billing_cards.values()), columns)

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

    def update_dashboard(self, today=None):
        if not self.desktop_ready:
            return
        today = today or datetime.now().astimezone().date()
        lowest = None
        for key, card in self.cards.items():
            value = self.pet_quotas.get(key, "조회 전")
            if value == "조회 실패" and card.data:
                value = card.amount.text() + " · 조회 실패"
            remaining = card.data[0]["remaining"] if card.data else None
            self.overview_quotas[key].setText(value)
            self.overview_quotas[key].setToolTip(card.summary_label.text() + ("\n" + card.status.text() if not card.status.isHidden() else ""))
            bar = self.overview_bars[key]
            bar.setVisible(remaining is not None)
            bar.setValue(round(remaining * 10) if remaining is not None else 0)
            bar.setAccessibleName(f"{account_title(self.account(key))} {value}")
            set_risk(bar, risk_level(remaining))
            # A failed lookup is a warning even while the last value is still shown.
            set_risk(self.overview_quotas[key], risk_level(remaining) or ("low" if card.error else ""))
            if remaining is not None and (lowest is None or remaining < lowest[0]):
                lowest = (remaining, key, card.data[0]["label"])
        self.metrics["lowest"].setText(f"{lowest[0]:g}%" if lowest else "—")
        self.metric_notes["lowest"].setText(f"{account_title(self.account(lowest[1]))} · {lowest[2]}" if lowest else "조회 전")
        set_risk(self.metrics["lowest"], risk_level(lowest[0]) if lowest else "")
        waiting = sum(task.get("status") == "waiting" for task in self.agent_activity)
        active = len(self.agent_activity)
        self.metrics["agents"].setText(str(active))
        self.metric_notes["agents"].setText(f"승인 대기 {waiting}건" if waiting else f"실행 중 {active}건" if active else "실행 중인 작업 없음")
        set_risk(self.metric_notes["agents"], "low" if waiting else "")
        self.update_billing_page(today)
        self.render_schedule()
        self.render_agents()

    def update_billing_page(self, today):
        records = {key: self.payment_record(key) for key in self.cards}
        dates = {key: next_payment(records[key], today) for key in self.cards}
        for key, card in self.cards.items():
            self.billing_plans[key].setText(self.plan_text(key))
            day = dates[key]
            self.billing_dates[key].setText(f"{day:%m/%d}" if day else "미설정")
            set_muted(self.billing_dates[key], not day)
            period = "ChatGPT 구독 만료일" if day and records[key].get("auto") else PERIODS[records[key]["period"]] if day else ""
            self.billing_notes[key].setText(f"{due_text(day, today)} · {period}" if day else "결제 기준일과 주기를 입력하세요")
            self.billing_buttons[key].setText("수정" if self.billing_record(key) else "결제일 설정")
            self.billing_prices[key].setText(price_summary(self.price_override(key), self.plan_price(key)))
            reset = nearest_reset(card.data)
            self.billing_resets[key].setText(f"가장 가까운 한도 초기화 · {reset['label']} · {time_left(reset['reset_at'])}"
                                             if reset else "한도 초기화 정보 없음")
        upcoming = {key: day for key, day in dates.items() if day and day >= today}
        soon = min(upcoming, key=upcoming.get) if upcoming else None
        count, total = sum(bool(day) for day in dates.values()), len(self.cards)
        if soon:
            self.metrics["billing"].setText(f"{upcoming[soon]:%m.%d}")
            self.metric_notes["billing"].setText(due_text(upcoming[soon], today))
            self.billing_summary.setText(f"설정된 구독 {count} / {total} · 다음 결제 {upcoming[soon]:%m/%d} ({due_text(upcoming[soon], today)}) · {account_title(self.account(soon))}")
        elif count:
            self.metrics["billing"].setText("확인 필요")
            self.metric_notes["billing"].setText("지난 결제일 수정 필요")
            self.billing_summary.setText(f"설정된 구독 {count} / {total} · 결제일 확인 필요")
        else:
            self.metrics["billing"].setText("미설정")
            self.metric_notes["billing"].setText("구독·결제에서 설정")
            self.billing_summary.setText("각 서비스의 결제일 설정에서 결제 기준일과 주기를 입력하면 다음 결제 예정일과 남은 날짜를 표시합니다. "
                                         "결제일 자동 조회·자동 결제는 하지 않습니다.")

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

    def render_agents(self, force=False):
        fields = ("id", "agent", "status", "label", "title", "model", "effort", "current_task", "todos", "subagents")
        signature = (self.theme, tuple((repr([task.get(name) for name in fields]), task.get("elapsed", 0) // 60)
                                       for task in self.agent_activity))
        if signature == self.task_signature and not force:
            return
        self.task_signature = signature
        for layout in (self.task_layout, self.overview_sessions):
            while layout.count():
                item = layout.takeAt(0).widget()
                item.hide()
                item.deleteLater()
        colors = THEMES[self.theme]
        self.expanded_sessions &= {str(task.get("id")) for task in self.agent_activity}
        self.task_buttons = []
        for index, task in enumerate(self.agent_activity):
            card = SessionCard(task, colors, str(task.get("id")) in self.expanded_sessions)
            card.activated.connect(lambda key=task.get("id"): self.open_activity(key))
            card.expanded_changed.connect(self.remember_expanded)
            self.task_layout.addWidget(card)
            self.task_buttons.append(card)
            if index < 3:
                summary = SessionCard(task, colors, compact=True)
                summary.activated.connect(lambda key=task.get("id"): self.open_activity(key))
                self.overview_sessions.addWidget(summary)
        self.agents_empty.setVisible(not self.task_buttons)
        extra = len(self.agent_activity) - 3
        self.overview_activity.setText("실행 중인 작업이 없습니다." if not self.agent_activity else
                                       f"외 {extra}개 작업은 작업 목록에서 확인하세요." if extra > 0 else "")
        self.overview_activity.setVisible(bool(self.overview_activity.text()))

    def remember_expanded(self, task_id, expanded):
        (self.expanded_sessions.add if expanded else self.expanded_sessions.discard)(task_id)

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
        neon = bool(c["glow"])
        accent, soft = c["accent"], c["accent_soft"]
        # Widget's generic risk rules lose to the ID selectors below, so repeat them per desktop widget.
        service_bars = "".join(f"QProgressBar#overviewBar[service='{key}']::chunk {{background:{c[key]};}} " for key in SERVICES)
        risk_rules = "".join(
            f"QLabel#metricValue[risk='{risk}'], QLabel#metricNote[risk='{risk}'], QLabel#overviewValue[risk='{risk}'] {{color:{c[token]};}} "
            f"QProgressBar#overviewBar[risk='{risk}']::chunk {{background:{c[token]};}} "
            for risk, token in (("low", "warning"), ("critical", "danger")))
        primary = (f"background:{rgba(accent, .1)}; border:1px solid {accent}; color:{c['core']};" if neon else
                   f"background:{accent}; border:1px solid {accent}; color:{c['card']};")
        self.setStyleSheet(self.styleSheet() + f"""
            QWidget#window {{background:{c['background']};}}
            QFrame#surface {{background:{c['background']}; border:0; border-radius:0;}}
            QFrame#sidebar {{background:{c['sidebar']}; border:0; border-right:1px solid {c['border']};}}
            QLabel#clock {{font-size:14px; color:{c['muted']}; font-weight:500;}}
            QLabel#desktopDescription {{font-size:12px; color:{c['muted']};}}
            QPushButton#sidebarButton {{text-align:left; padding:10px 12px 10px 18px; background:transparent; border:0; border-radius:10px; font-size:13px; color:{c['muted'] if neon else c['text']};}}
            QPushButton#sidebarButton:hover {{background:{c['control']}; color:{c['text']};}}
            QPushButton#sidebarButton:checked {{background:{soft}; color:{c['core'] if neon else accent}; font-weight:650;}}
            QToolButton#sidebarSettings {{text-align:left; padding:8px 14px; background:transparent; border:1px solid {c['border']};}}
            QFrame#desktopPanel {{background:{c['card']}; border:1px solid {c['border']}; border-radius:16px;}}
            QLabel#panelTitle {{font-size:13px; font-weight:650;}}
            QLabel#metricValue {{font-family:{DISPLAY_CSS}; font-size:30px; font-weight:600; color:{c['core']};}}
            QLabel#metricNote {{font-size:11px; color:{c['muted']};}}
            QLabel#overviewName {{font-size:12px; padding:5px 0;}}
            QLabel#overviewValue {{font-size:12px; font-weight:650;}}
            QLabel#scheduleSummary {{font-size:13px; padding:4px 0;}}
            QPushButton#desktopTextButton {{background:transparent; color:{accent}; padding:5px 0; border:0; font-size:12px;}}
            QPushButton#desktopTextButton:hover {{color:{c['core'] if neon else accent}; text-decoration:underline;}}
            QPushButton#desktopPrimary {{{primary} font-weight:650; padding:8px 16px;}}
            QPushButton#desktopPrimary:hover {{background:{rgba(accent, .22) if neon else accent};}}
            QFrame#sessionCard {{background:{c['background'] if neon else c['card']}; border:1px solid {c['border']}; border-radius:14px;}}
            QLabel#sessionTitle {{font-size:14px; font-weight:700;}}
            QLabel#statusPill {{color:{accent}; background:{rgba(accent, .1)}; border:1px solid {rgba(accent, .45)}; border-radius:9px; padding:2px 9px; font-size:11px; font-weight:650;}}
            QLabel#statusPill[waiting='true'] {{color:{c['warning']}; background:{rgba(c['warning'], .1)}; border-color:{rgba(c['warning'], .55)};}}
            QLabel#modelChip {{background:{c['control']}; border:1px solid {c['border']}; border-radius:8px; padding:1px 8px; font-size:10px; font-weight:600;}}
            QLabel#currentTask {{font-size:12px;}}
            QToolButton#todoToggle {{background:transparent; border:0; color:{accent}; font-size:11px; font-weight:600; padding:0 2px;}}
            QLabel#agentChip {{background:{c['control'] if neon else c['background']}; border:1px solid {c['border']}; border-radius:9px; padding:2px 9px; font-size:10px;}}
            QLabel#todoList {{font-size:11px; padding:2px 0 0 4px;}}
            QLabel#billingSummary {{font-size:13px;}}
            QLabel#billingDate {{font-family:{DISPLAY_CSS}; font-size:26px; font-weight:600; color:{c['core']};}}
            QLabel#billingDate[muted='true'] {{color:{c['muted']};}}
        """ + service_bars + risk_rules)
        self.update_navigation_icons()
        self.render_agents()

    def update_navigation_icons(self):
        colors = THEMES[self.theme]
        for key, button in self.nav_buttons.items():
            button.setIcon(navigation_icon(key, colors["accent"] if button.isChecked() else colors["muted"]))
