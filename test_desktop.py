"""Desktop integration checks; all network and real settings stay mocked."""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QSettings, Qt, QEvent, QCoreApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from widget import Widget
from desktop import DesktopWindow


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.directory.name)/"settings.ini"), QSettings.IniFormat)
        for item in (patch("widget.QSettings", return_value=self.settings),
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
        for key in ("quota", "calendar", "agents", "billing", "home"):
            QTest.mouseClick(self.window.nav_buttons[key], Qt.LeftButton)
            self.assertEqual(self.window.current_page, key)
            self.assertEqual(self.window.size(), size)
        self.assertEqual(self.window.cards["codex"].amount.text(), "80%")
        self.assertIn("80%", self.window.overview_quotas["codex"].text())
        self.window.received("codex", None, "네트워크 오류")
        self.assertIn("조회 실패", self.window.overview_quotas["codex"].text())

    def test_billing_is_independent_persistent_and_survives_quota_failure(self):
        from billing import BillingDialog
        def save(dialog):
            dialog.date_input.setDate(datetime.now().date() + timedelta(days=10))
            dialog.period_input.setCurrentIndex(0)
            return BillingDialog.Accepted
        with patch.object(BillingDialog, "exec", save):
            self.window.edit_billing("claude_max")
        self.assertIn("10일 후", self.window.cards["claude_max"].billing.text())
        self.assertIn("미설정", self.window.cards["claude"].billing.text())
        self.window.received("claude_max", None, "로그인 필요")
        self.assertIn("10일 후", self.window.cards["claude_max"].billing.text())
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
        self.assertIn("승인 대기", self.window.task_buttons[0].text())
        self.assertIn("3분", self.window.task_buttons[0].text())
        with patch("widget.XWindows") as windows:
            windows.return_value.candidates.return_value=[(123,"Claude terminal")]
            QTest.mouseClick(self.window.task_buttons[0], Qt.LeftButton)
            windows.return_value.activate.assert_called_once_with(123)

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
