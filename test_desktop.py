"""Desktop integration checks; all network and real settings stay mocked."""
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QSettings, Qt, QEvent, QCoreApplication, QPoint
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel
from widget import Widget, THEMES, NeonBar
from desktop import DesktopWindow
from test_widget import seed_accounts


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.directory.name)/"settings.ini"), QSettings.IniFormat)
        seed_accounts(self.settings)
        for item in (patch("widget.QSettings", return_value=self.settings),
                     patch.dict(os.environ, {"XDG_CONFIG_HOME": self.directory.name}),
                     patch.object(Widget, "refresh"), patch.object(Widget, "refresh_activity"),
                     patch("calendar_panel.CalendarPanel.refresh")):
            item.start()
            self.addCleanup(item.stop)
        self.window = DesktopWindow()
        self.window.show()
        self.app.processEvents()
        self.window.received("codex", {"rows": [{"label": "Codex · 5시간", "remaining": 25, "reset": "오늘 초기화"},
                                                 {"label": "Codex · 주간", "remaining": 80, "reset": "주간 초기화"}], "plan": "pro"}, "")

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        # Drain Qt's deferred deletes before the next test's animation clock starts.
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.directory.cleanup()

    def test_desktop_navigation_keeps_live_data_and_window_size(self):
        self.assertEqual(self.window.menu.windowType(), Qt.Popup)
        self.assertFalse(self.window.windowFlags() & Qt.FramelessWindowHint)
        self.assertEqual(self.window.current_page, "home")
        size = self.window.size()
        self.assertGreaterEqual(size.width(), 900)
        for key in ("quota", "calendar", "todo", "agents", "billing", "home"):
            QTest.mouseClick(self.window.nav_buttons[key], Qt.LeftButton)
            self.assertEqual(self.window.current_page, key)
            self.assertEqual(self.window.size(), size)
        self.assertEqual(self.window.cards["codex"].amount.text(), "80%")
        self.assertIn("80%", self.window.overview_quotas["codex"].text())
        self.window.received("codex", None, "네트워크 오류")
        self.assertIn("조회 실패", self.window.overview_quotas["codex"].text())

    def test_desktop_todo_uses_the_same_saved_list(self):
        self.window.set_page("todo")
        self.assertTrue(self.window.todo_scroll.isVisible())
        self.assertFalse(self.window.quick_add.isVisible())
        self.window.todo.input.setText("전화하기")
        QTest.keyClick(self.window.todo.input, Qt.Key_Return)
        self.assertEqual(self.window.todo.items[0]["text"], "전화하기")

    def test_billing_is_independent_persistent_and_survives_quota_failure(self):
        from billing import BillingDialog
        def save(dialog):
            dialog.date_input.setDate(datetime.now().date() + timedelta(days=10))
            dialog.period_input.setCurrentIndex(0)
            return BillingDialog.Accepted
        with patch.object(BillingDialog, "exec", save):
            self.window.edit_billing("claude_max")
        self.assertIn("D-10", self.window.cards["claude_max"].billing.text())
        self.assertIn("미설정", self.window.cards["claude"].billing.text())
        self.window.received("claude_max", None, "로그인 필요")
        self.assertIn("D-10", self.window.cards["claude_max"].billing.text())
        restored = DesktopWindow()
        self.assertEqual(restored.cards["claude_max"].billing.text(), self.window.cards["claude_max"].billing.text())
        restored.close()
        restored.deleteLater()
        self.window.save_billing("claude_max", None)
        self.assertIn("미설정", self.window.cards["claude_max"].billing.text())

    def test_pet_restores_desktop_size_and_normal_window_flags(self):
        before = self.window.size()
        self.window.set_page("billing")
        self.window.toggle_collapsed()
        self.assertTrue(self.window.collapsed)
        self.assertTrue(self.window.windowFlags() & Qt.WindowStaysOnTopHint)
        self.assertEqual(self.window.width(), 108)
        self.app.processEvents()
        self.assertEqual(self.window.grab().toImage().pixelColor(0, 0).alpha(), 0)
        self.window.toggle_collapsed()
        self.assertFalse(self.window.collapsed)
        self.assertEqual(self.window.size(), before)
        self.assertEqual(self.window.current_page, "billing")
        self.assertFalse(self.window.windowFlags() & Qt.FramelessWindowHint)
        self.assertFalse(self.window.windowFlags() & Qt.WindowStaysOnTopHint)

    def test_pet_keeps_native_alpha_after_repeated_mode_changes(self):
        def check_surface():
            self.assertTrue(self.window.testAttribute(Qt.WA_TranslucentBackground))
            # The offscreen plugin has no native visual; X11 must use an alpha surface.
            if self.app.platformName() == "xcb":
                self.assertGreaterEqual(self.window.windowHandle().format().alphaBufferSize(), 8)

        for theme in ("light", "dark", "light"):
            self.window.set_theme(theme)
            check_surface()
            self.window.toggle_collapsed()
            self.app.processEvents()
            check_surface()
            self.assertEqual(self.window.grab().toImage().pixelColor(0, 0).alpha(), 0)
            self.window.toggle_collapsed()
            self.app.processEvents()
            check_surface()

    def test_task_list_opens_detected_window_and_exposes_waiting(self):
        task = dict(id="test", agent="claude", label="승인 대기", status="waiting",
                    started=datetime.now().timestamp()-180, updated=datetime.now().timestamp(), target="/tmp/task")
        self.window.received_activity([task])
        self.window.set_page("agents")
        self.assertEqual(len(self.window.task_buttons), 1)
        self.assertIn("승인 대기", self.window.task_buttons[0].accessibleName())
        self.assertIn("3분", self.window.task_buttons[0].accessibleName())
        with patch("widget.XWindows") as windows:
            windows.return_value.candidates.return_value=[(123,"Claude terminal")]
            QTest.mouseClick(self.window.task_buttons[0], Qt.LeftButton)
            windows.return_value.activate.assert_called_once_with(123)

    def test_session_cards_show_title_model_todos_and_subagents(self):
        from desktop import SessionCard
        now = datetime.now().timestamp()
        task = dict(id="s1", agent="claude", label="파일 수정 중", status="running", started=now - 600, updated=now,
                    target="/tmp/s1", title="<네온> 리디자인", model="opus 5.5", effort="high",
                    current_task="세션 카드 그리기",
                    todos=[{"text": "토큰", "status": "completed"}, {"text": "세션 카드 그리기", "status": "in_progress"},
                           {"text": "README", "status": "pending"}],
                    subagents=[{"type": "Explore", "description": "find", "model": "haiku 5.5", "effort": "low", "status": "running"},
                               {"type": "reviewer", "model": "opus 5.5", "effort": None, "status": "running"}])
        unknown = dict(id="u1", agent="gemini", label="작업 중", status="waiting", started=now - 60, updated=now, target="/tmp/u1")
        self.window.received_activity([task, unknown])
        self.window.set_page("agents")
        self.app.processEvents()
        card, other = self.window.task_buttons
        texts = [item.text() for item in card.findChildren(QLabel)]
        self.assertIn("<네온> 리디자인", texts)
        self.assertIn("opus 5.5 · high", texts)
        self.assertIn("● 작업 중", texts)
        self.assertIn("▸ 세션 카드 그리기", texts)
        self.assertTrue(any("Explore · haiku 5.5 · low" in text for text in texts))
        self.assertTrue(any("reviewer · opus 5.5" in text for text in texts))
        self.assertEqual(card.toggle.text(), "할 일 1/3 ▾")
        self.assertTrue(card.todo_list.isHidden())
        probe = SessionCard(dict(task, todos=[{"text": "<b>", "status": "pending"}]), THEMES["dark"])
        self.assertIn("&lt;b&gt;", probe.todo_list.text())
        probe.deleteLater()
        card.toggle.click()
        self.assertTrue(card.todo_list.isVisible())
        self.assertIn("<s>토큰</s>", card.todo_list.text())
        self.assertEqual(card.findChild(NeonBar, "todoProgress").value(), 333)
        self.assertIn("● 승인·입력 대기", [item.text() for item in other.findChildren(QLabel)])
        self.assertIn("gemini", other.accessibleName())
        # A refresh with new data rebuilds the cards but keeps the opened todo list.
        self.window.received_activity([dict(task, current_task="README"), unknown])
        self.app.processEvents()
        self.assertTrue(self.window.task_buttons[0].todo_list.isVisible())
        self.assertEqual(self.window.overview_sessions.count(), 2)
        for theme in ("dark", "light"):
            self.window.set_theme(theme)
            self.app.processEvents()
            self.assertFalse(self.window.task_buttons[0].grab().isNull())
        with patch("widget.XWindows") as windows:
            windows.return_value.candidates.return_value = [(7, "Claude terminal")]
            QTest.mouseClick(self.window.task_buttons[0], Qt.LeftButton, pos=QPoint(8, 8))
            windows.return_value.candidates.assert_called_once_with("claude", "/tmp/s1")

    def test_opening_existing_app_preserves_maximized_window(self):
        self.window.showMaximized()
        self.app.processEvents()
        self.assertTrue(self.window.isMaximized())
        self.window.bring_to_front()
        self.assertTrue(self.window.isMaximized())

    def test_calendar_summary_keeps_tomorrow_separate_from_today(self):
        now = datetime.now().astimezone()
        tomorrow = now + timedelta(days=1)
        self.window.calendar.loaded_day = now.date()
        self.window.calendar.view = "tomorrow"
        self.window.calendar.events = [{"summary": "내일 회의", "start": {"dateTime": tomorrow.isoformat()},
                                        "end": {"dateTime": (tomorrow + timedelta(hours=1)).isoformat()}}]
        self.window.update_dashboard()
        self.assertEqual(self.window.metrics["events"].text(), "0")
        self.assertIn("내일 회의", self.window.overview_schedule.text())
        self.window.calendar.view = "week"
        self.window.calendar.events.append({"summary": "오늘 종일", "start": {"date": now.date().isoformat()},
                                            "end": {"date": tomorrow.date().isoformat()}})
        self.window.update_dashboard()
        self.assertEqual(self.window.metrics["events"].text(), "1")
        self.assertIn("오늘 종일", self.window.overview_schedule.text())

    def test_dashboard_today_count_matches_calendar_today_view_in_every_range(self):
        now = datetime.now().astimezone()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        calendar = self.window.calendar
        calendar.loaded_day = now.date()
        calendar.events = [{"summary": "야간 작업", "start": {"dateTime": (midnight - timedelta(hours=1)).isoformat()},
                            "end": {"dateTime": (midnight + timedelta(seconds=1)).isoformat()}}]
        for index in range(calendar.range_select.count()):
            calendar.range_select.setCurrentIndex(index)
            self.window.update_dashboard()
            self.assertEqual(self.window.metrics["events"].text(), "1")
        calendar.range_select.setCurrentIndex(0)
        self.assertEqual(calendar.event_layout.count(), 1)

    def test_maximized_window_closed_in_pet_mode_reopens_maximized(self):
        self.window.showMaximized()
        self.app.processEvents()
        self.window.toggle_collapsed()
        self.app.processEvents()
        self.window.close()
        restored = DesktopWindow()
        restored.show()
        self.app.processEvents()
        self.assertTrue(restored.isMaximized())
        restored.close()
        restored.deleteLater()

    def test_billing_cancel_and_invalid_saved_data_do_not_change_other_accounts(self):
        from billing import BillingDialog
        self.window.save_billing("claude", {"date": "2026-10-15", "period": "monthly"})
        before = self.window.cards["claude"].billing.text()
        with patch.object(BillingDialog, "exec", return_value=BillingDialog.Rejected):
            self.window.edit_billing("claude")
        self.assertEqual(self.window.cards["claude"].billing.text(), before)
        self.settings.setValue("billing/claude_max", "invalid JSON")
        self.window.update_billing()
        self.assertIn("미설정", self.window.cards["claude_max"].billing.text())
        self.assertEqual(self.window.cards["claude"].billing.text(), before)

    def test_desktop_hides_quota_view_switch(self):
        self.settings.setValue("quota_view", "list")
        desktop = DesktopWindow()
        desktop.show()
        desktop.set_page("quota")
        self.app.processEvents()
        self.assertTrue(desktop.quota_view_switch.isHidden())
        self.assertTrue(desktop.quota_list.isHidden())
        self.assertTrue(desktop.scroll.isVisible())
        self.assertEqual(self.settings.value("quota_view"), "list")
        desktop.close()
        desktop.deleteLater()

    def test_quota_grid_and_themes_fit_small_and_large_windows(self):
        self.window.set_page("quota")
        for width, columns in ((1200, 2), (820, 1)):
            self.window.resize(width, 760)
            self.app.processEvents()
            self.assertEqual(self.window.quota_columns, columns)
            self.assertEqual(self.window.scroll.horizontalScrollBar().maximum(), 0)
        for mode in ("light", "dark"):
            self.window.set_theme(mode)
            self.window.resize(1160, 800)
            self.window.set_page("home")
            self.app.processEvents()
            self.assertTrue(self.window.grab().save("/tmp/check-ai-desktop-"+mode+".png"))

    def test_dashboard_lowest_quota_metric_and_risk(self):
        window = self.window
        window.set_theme("light")
        colors = THEMES["light"]
        self.assertNotIn("accounts", window.metrics)
        self.assertEqual((window.metrics["lowest"].text(), window.metric_notes["lowest"].text()), ("80%", "GPT / Codex · Codex · 주간"))
        self.assertEqual(window.metrics["lowest"].property("risk"), "")
        self.assertEqual(window.metric_notes["events"].text(), "오늘 기준")
        for remaining, risk, token in ((15, "low", "warning"), (7, "critical", "danger")):
            window.received("antigravity", {"rows": [{"label": "Gemini · 주간", "remaining": remaining, "reset": "주간 초기화"}], "plan": None}, "")
            self.app.processEvents()
            self.assertEqual((window.metrics["lowest"].text(), window.metric_notes["lowest"].text()),
                             (f"{remaining}%", "Antigravity · Gemini · 주간"))
            self.assertEqual(window.metrics["lowest"].property("risk"), risk)
            self.assertEqual(window.metrics["lowest"].palette().color(QPalette.WindowText), QColor(colors[token]))
            self.assertEqual(window.overview_quotas["antigravity"].property("risk"), risk)
            self.assertEqual(window.overview_quotas["antigravity"].palette().color(QPalette.WindowText), QColor(colors[token]))
            bar = window.overview_bars["antigravity"]
            self.assertEqual((bar.property("risk"), bar.height(), bar.value()), (risk, 4, remaining * 10))
            self.assertEqual(bar.grab().toImage().pixelColor(2, 2), QColor(colors[token]))
        codex_bar = window.overview_bars["codex"]
        self.assertEqual((codex_bar.property("risk"), codex_bar.value()), ("", 800))
        self.assertEqual(codex_bar.grab().toImage().pixelColor(2, 2), QColor(colors["codex"]))
        # Unlimited rows have no percentage and stay out of the minimum.
        window.received("copilot", {"rows": [{"label": "채팅", "remaining": None, "reset": ""}], "plan": None}, "")
        self.assertEqual(window.metrics["lowest"].text(), "7%")
        self.assertTrue(window.overview_bars["copilot"].isHidden())
        for card in window.cards.values():
            card.data = []
        window.update_dashboard()
        self.assertEqual((window.metrics["lowest"].text(), window.metric_notes["lowest"].text()), ("—", "조회 전"))
        self.assertEqual(window.metrics["lowest"].property("risk"), "")

    def test_dashboard_agents_metric_shows_waiting(self):
        window = self.window
        window.set_theme("light")
        self.assertEqual((window.metrics["agents"].text(), window.metric_notes["agents"].text()), ("0", "실행 중인 작업 없음"))
        now = datetime.now().timestamp()
        task = lambda name, status: dict(id=name, agent="claude", label=name, status=status, started=now-60, updated=now, target="/tmp/" + name)
        with patch.object(Widget, "send_notification"):
            window.received_activity([task("a", "running")])
            self.assertEqual((window.metrics["agents"].text(), window.metric_notes["agents"].text()), ("1", "실행 중 1건"))
            self.assertEqual(window.metric_notes["agents"].property("risk"), "")
            window.received_activity([task("a", "running"), task("b", "waiting")])
        self.app.processEvents()
        self.assertEqual((window.metrics["agents"].text(), window.metric_notes["agents"].text()), ("2", "승인 대기 1건"))
        self.assertEqual(window.metric_notes["agents"].property("risk"), "low")
        self.assertEqual(window.metric_notes["agents"].palette().color(QPalette.WindowText), QColor(THEMES["light"]["warning"]))
        with patch.object(Widget, "send_notification"):
            window.received_activity([task("a", "running"), task("b", "running")])
        self.assertEqual((window.metric_notes["agents"].text(), window.metric_notes["agents"].property("risk")), ("실행 중 2건", ""))

    def test_billing_page_summary_and_empty_state(self):
        window = self.window
        today = date(2026, 10, 2)
        guidance = ("각 서비스의 결제일 설정에서 결제 기준일과 주기를 입력하면 다음 결제 예정일과 남은 날짜를 표시합니다. "
                    "결제일 자동 조회·자동 결제는 하지 않습니다.")
        window.update_dashboard(today)
        self.assertEqual(window.billing_summary.text(), guidance)
        self.assertEqual((window.metrics["billing"].text(), window.metric_notes["billing"].text()), ("미설정", "구독·결제에서 설정"))
        for key in window.cards:
            self.assertEqual((window.billing_dates[key].text(), window.billing_buttons[key].text()), ("미설정", "결제일 설정"))
        window.save_billing("claude_max", {"date": "2026-10-15", "period": "monthly"})
        window.save_billing("codex", {"date": "2026-10-20", "period": "once"})
        window.update_dashboard(today)
        self.assertEqual(window.billing_summary.text(), "설정된 구독 2 / 5 · 다음 결제 10/15 (13일 후) · Claude · Max 5x")
        self.assertEqual((window.billing_dates["claude_max"].text(), window.billing_notes["claude_max"].text(), window.billing_buttons["claude_max"].text()),
                         ("10/15", "13일 후 · 매월", "수정"))
        self.assertEqual((window.billing_dates["codex"].text(), window.billing_notes["codex"].text()), ("10/20", "18일 후 · 반복 없음"))
        self.assertEqual((window.billing_dates["claude"].text(), window.billing_buttons["claude"].text()), ("미설정", "결제일 설정"))
        self.assertEqual((window.metrics["billing"].text(), window.metric_notes["billing"].text()), ("10.15", "13일 후"))
        window.update_dashboard(date(2026, 10, 15))
        self.assertEqual(window.billing_summary.text(), "설정된 구독 2 / 5 · 다음 결제 10/15 (오늘) · Claude · Max 5x")
        self.assertEqual(window.billing_notes["claude_max"].text(), "오늘 · 매월")
        self.assertEqual(window.metric_notes["billing"].text(), "오늘")
        window.update_dashboard(date(2026, 10, 25))
        self.assertEqual(window.billing_notes["codex"].text(), "확인 필요 · 반복 없음")
        self.assertEqual(window.billing_summary.text(), "설정된 구독 2 / 5 · 다음 결제 11/15 (21일 후) · Claude · Max 5x")
        window.save_billing("claude_max", None)
        window.update_dashboard(date(2026, 10, 25))
        self.assertEqual(window.billing_summary.text(), "설정된 구독 1 / 5 · 결제일 확인 필요")
        self.assertEqual((window.metrics["billing"].text(), window.metric_notes["billing"].text()), ("확인 필요", "지난 결제일 수정 필요"))

    def test_billing_cards_reflow_two_columns(self):
        window = self.window
        window.set_page("billing")
        for width, columns in ((1160, 2), (900, 1), (1060, 2), (1059, 1)):
            window.resize(width, 800)
            self.app.processEvents()
            self.assertEqual(window.billing_columns, columns, width)
            grid = window.billing_grid
            cells = [grid.getItemPosition(grid.indexOf(box))[:2] for box in window.billing_cards.values()]
            self.assertEqual(sorted(cells), [(i // columns, i % columns) for i in range(len(cells))])
            self.assertEqual(window.billing_page.horizontalScrollBar().maximum(), 0)
            for box in window.billing_cards.values():
                self.assertLessEqual(box.geometry().right(), window.billing_page.viewport().width())
