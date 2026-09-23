"""Run with QT_QPA_PLATFORM=offscreen. No network or real settings are touched."""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings, QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from widget import Widget, activate_existing, listen_for_activation
from pixel_pet import DURATIONS, animation_row, display_row


ROWS = {
    "codex": [{"label": "Codex · 주간", "remaining": 43, "reset": "09/15 10:28 초기화"},
              {"label": "GPT-5.3-Codex-Spark · 5시간", "remaining": 100, "reset": "09/11 13:20 초기화"}],
    "antigravity": [{"label": "Gemini · 주간", "remaining": 93, "reset": "09/11 09:02 초기화"},
                    {"label": "Claude and GPT · 5시간", "remaining": 100, "reset": "09/11 13:20 초기화"}],
    "copilot": [{"label": "채팅", "remaining": None, "reset": ""},
                {"label": "프리미엄", "remaining": 100, "detail": "200/200 크레딧", "reset": "10/01 초기화 · 시각 미제공"}],
}


class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.directory.name) / "settings.ini"), QSettings.IniFormat)
        self.settings_patch = patch("widget.QSettings", return_value=self.settings)
        self.refresh_patch = patch.object(Widget, "refresh")
        self.calendar_refresh_patch = patch("calendar_panel.CalendarPanel.refresh")
        self.settings_patch.start()
        self.refresh_patch.start()
        self.calendar_refresh_patch.start()
        activity_patch = patch.object(Widget, 'refresh_activity')
        activity_patch.start()
        self.addCleanup(activity_patch.stop)
        self.window = Widget()
        self.window.resize(430, 780)
        for key, rows in ROWS.items():
            self.window.received(key, {"rows": rows, "plan": {"codex": "prolite", "copilot": "individual"}.get(key)}, "")
        self.window.show()
        self.window.set_page("quota")
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.refresh_patch.stop()
        self.calendar_refresh_patch.stop()
        self.settings_patch.stop()
        self.directory.cleanup()

    def test_circle_collapse_and_restore(self):
        self.window.resize(430, 700)
        self.window.set_page("calendar")
        self.window.set_transparency(30)
        original = self.window.geometry()
        self.assertNotIn("항상 위에 표시", [a.text() for a in self.window.menu.actions()])
        QTest.mouseClick(self.window.collapse_button, Qt.LeftButton)
        QTest.qWait(250)
        self.app.processEvents()
        self.assertTrue(self.window.collapsed)
        self.assertFalse(self.window.surface.isVisible())
        self.assertTrue(self.window.restore_button.isVisible())
        self.assertEqual((self.window.width(), self.window.height()), (108, 108))
        self.assertTrue(self.window.windowFlags() & Qt.WindowStaysOnTopHint)
        self.assertEqual(self.window.windowOpacity(), 1)
        QTest.mouseClick(self.window.restore_button, Qt.LeftButton)
        for _ in range(20):
            if self.window.fold_transition.progress == 1.0:
                break
            QTest.qWait(50)
        self.app.processEvents()
        self.assertEqual(self.window.geometry(), original)
        self.assertTrue(self.window.windowFlags() & Qt.WindowStaysOnTopHint)
        self.assertTrue(self.window.calendar.isVisible())
        self.assertAlmostEqual(self.window.windowOpacity(), .7, places=2)
        self.window.toggle_collapsed()
        self.window.close()
        self.assertEqual(self.settings.value("geometry"), self.window.expanded_geometry)
        self.window.bring_to_front()
        self.assertFalse(self.window.collapsed)
        self.assertEqual(self.window.geometry(), original)

    def test_drag_circle_does_not_activate_and_home_restores_feature_size(self):
        self.assertEqual(self.window.timer.interval(), 180_000)
        self.window.resize(430, 650)
        self.window.set_page("home")
        self.assertEqual(self.window.height(), 440)
        self.window.set_page("quota")
        self.assertEqual(self.window.height(), 650)
        self.window.toggle_collapsed()
        start = self.window.pos()
        QTest.mousePress(self.window.restore_button, Qt.LeftButton, pos=QPoint(20, 20))
        QTest.mouseMove(self.window.restore_button, QPoint(-20, 60))
        QTest.mouseRelease(self.window.restore_button, Qt.LeftButton, pos=QPoint(20, 20))
        self.assertTrue(self.window.collapsed)
        self.assertNotEqual(self.window.pos(), start)

    def test_circle_motion_respects_reduced_motion_and_keyboard(self):
        button = self.window.collapse_button
        QTest.mousePress(button, Qt.LeftButton)
        QTest.qWait(80)
        self.assertLess(button.scale, 1.0)
        QTest.mouseRelease(button, Qt.LeftButton)
        self.assertTrue(self.window.collapsed)
        self.assertLess(self.window.restore_button.scale, 1.0)
        QTest.qWait(250)
        self.assertEqual(self.window.restore_button.scale, 1.0)

        image = self.window.grab().toImage()
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        image.save("/tmp/ai-quota-widget-circle.png")
        self.window.reduce_motion.setChecked(True)
        QTest.mouseClick(self.window.restore_button, Qt.LeftButton)
        self.assertEqual(button.scale, 1.0)
        self.window.reduce_motion.setChecked(False)
        button.setFocus()
        QTest.keyClick(button, Qt.Key_Space)
        self.assertTrue(self.window.collapsed)
        self.assertEqual(self.window.restore_button.scale, 1.0)

    def test_reveal_can_be_interrupted_and_respects_opacity(self):
        self.window.set_transparency(40)
        QTest.mouseClick(self.window.collapse_button, Qt.LeftButton)
        self.assertLess(self.window.windowOpacity(), 1)
        QTest.mouseClick(self.window.restore_button, Qt.LeftButton)
        for _ in range(20):
            if self.window.fold_transition.progress == 1.0:
                break
            QTest.qWait(50)
        self.assertFalse(self.window.collapsed)
        self.assertAlmostEqual(self.window.windowOpacity(), .6, places=2)
        QTest.mouseClick(self.window.collapse_button, Qt.LeftButton)
        self.window.reduce_motion.setChecked(True)
        self.assertEqual(self.window.windowOpacity(), 1)
        QTest.mouseClick(self.window.restore_button, Qt.LeftButton)
        self.assertAlmostEqual(self.window.windowOpacity(), .6, places=2)

    def test_computer_pet_states_and_idle_lifecycle(self):
        pet = self.window.restore_button
        self.assertFalse(pet.idle.isActive())
        self.window.toggle_collapsed()
        self.assertTrue(pet.idle.isActive())
        self.assertEqual(pet.mood(), "happy")
        self.window.pet_low["codex"] = True
        self.assertEqual(pet.mood(), "low")
        now = datetime.now().astimezone()
        self.window.calendar.loaded_day = now.date()
        self.window.calendar.events = [{"start": {"dateTime": (now + timedelta(minutes=5)).isoformat()}}]
        self.assertEqual(pet.mood(), "schedule")
        self.window.calendar.events = []
        self.window.pet_low.clear()
        pet.grab().save("/tmp/computer-pet.png")
        self.window.toggle_collapsed()
        self.assertFalse(pet.idle.isActive())

    def test_pixel_pet_frames_and_bubble_summary(self):
        self.window.toggle_collapsed()
        pet = self.window.restore_button
        pet.frame = 0
        normal = pet.grab().toImage()
        pet.frame = 1
        self.assertNotEqual(normal, pet.grab().toImage())
        self.window.reduce_motion.setChecked(True)
        reduced = pet.grab().toImage()
        pet.frame = 14
        self.assertEqual(reduced, pet.grab().toImage())
        self.window.calendar.loaded_day = datetime.now().astimezone().date()
        self.window.calendar.events = [{"summary": "<b>회의</b>", "start": {"dateTime": (datetime.now().astimezone() + timedelta(hours=1)).isoformat()}}]
        summary = self.window.pet_summary()
        self.assertIn("43% 남음", summary)
        self.assertIn("&lt;b&gt;회의&lt;/b&gt;", summary)
        self.assertNotIn("<b>회의</b>", summary)
        pet.show_bubble()
        self.assertTrue(pet.bubble.isVisible())
        self.assertTrue(pet.screen().availableGeometry().contains(pet.bubble.geometry()))
        pet.bubble.grab().save("/tmp/pet-bubble.png")
        self.window.toggle_collapsed()
        self.assertFalse(pet.bubble.isVisible())

    def test_all_source_animations_play_once_and_return_to_live_state(self):
        self.window.toggle_collapsed()
        pet = self.window.restore_button
        for row, durations in enumerate(DURATIONS):
            pet.play_animation(row)
            self.assertEqual(animation_row(pet), row)
            self.assertEqual(pet.frame, 0)
            for index, duration in enumerate(durations):
                self.assertEqual(pet.idle.interval(), duration)
                self.assertFalse(pet.grab().isNull())
                pet.tick()
            self.assertIsNone(pet.preview_row)
        self.window.reduce_motion.setChecked(True)
        pet.play_animation(4)
        self.assertEqual(pet.preview_row, 4)
        self.assertEqual(animation_row(pet), 4)
        for _ in DURATIONS[4]:
            pet.tick()
        self.assertIsNone(pet.preview_row)
        self.assertEqual(pet.frame, 0)
        self.window.reduce_motion.setChecked(False)
        with patch.object(self.window.calendar, "job", object()):
            self.assertEqual(animation_row(pet), 7)

    def test_drag_direction_reversal_and_jitter(self):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent
        self.window.toggle_collapsed()
        pet = self.window.restore_button
        QTest.mousePress(pet, Qt.LeftButton, pos=QPoint(20, 20))
        origin = pet.drag_start
        for dx, expected in ((-40, 2), (-20, 1), (-22, 1), (-30, 2)):
            event = QMouseEvent(QEvent.MouseMove, QPointF(20, 20), QPointF(origin + QPoint(dx, 0)), Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
            pet.mouseMoveEvent(event)
            self.assertEqual(animation_row(pet), expected)
            self.assertEqual(pet.frame, 0)
        QTest.mouseRelease(pet, Qt.LeftButton)
        self.assertTrue(self.window.collapsed)
        self.window.reduce_motion.setChecked(True)
        self.assertIsNone(pet.preview_row)
        self.window.toggle_collapsed()
        self.assertFalse(pet.idle.isActive())

    def test_codex_three_cycles_then_slow_idle(self):
        self.window.toggle_collapsed()
        self.window.reduce_motion.setChecked(False)
        pet = self.window.restore_button
        with patch.object(pet, "underMouse", return_value=False):
            for row in (3, 5, 6, 7, 8):
                pet.set_status(row)
                for _ in range(3 * len(DURATIONS[row])):
                    self.assertEqual(display_row(pet), row)
                    pet.tick()
                self.assertEqual(display_row(pet), 0)
                self.assertEqual(pet.idle.interval(), DURATIONS[0][0] * 6)
                for _ in range(12):
                    pet.tick()
                    self.assertEqual(display_row(pet), 0)
            with patch.object(pet, "underMouse", return_value=True):
                self.assertEqual(animation_row(pet), 4)
            self.window.reduce_motion.setChecked(True)
            pet.set_status(5)
            for _ in range(30):
                pet.tick()
            self.assertEqual(display_row(pet), 5)
            self.assertEqual(pet.frame, 0)

    def test_agent_activity_bubble(self):
        self.window.toggle_collapsed()
        pet = self.window.restore_button
        with patch.object(pet, 'underMouse', return_value=False):
            tasks = [dict(id=key, agent=key, label=label, status='waiting' if key == 'copilot' else 'running', started=datetime.now().timestamp()-180, updated=2, target='/tmp/test')
                     for key, label in [('codex', '파일 수정 중'), ('antigravity', '<회의>'), ('copilot', '승인·입력 대기')]]
            self.window.received_activity(tasks)
            self.assertTrue(pet.bubble.isVisible())
            self.assertEqual(pet.bubble.text.text().count('<img '), 3)
            self.assertIn('&lt;회의&gt;', pet.bubble.text.text())
            self.assertTrue(pet.bubble.waiting)
            self.assertIn('경과', pet.bubble.text.text())
            with patch('widget.XWindows') as windows:
                windows.return_value.candidates.return_value = [(123, 'Terminator')]
                pet.bubble.text.linkActivated.emit('1')
                windows.return_value.candidates.assert_called_once_with('antigravity', '/tmp/test')
                windows.return_value.activate.assert_called_once_with(123)
                windows.return_value.close.assert_called_once()
            for theme in ('light', 'dark'):
                self.window.set_theme(theme)
                pet.bubble.grab().save('/tmp/agent-bubble-' + theme + '.png')
            self.window.received_activity([])
            self.assertTrue(pet.bubble.isVisible())
            self.window.received_activity([dict(task, status='cancelled') for task in tasks])
            self.assertFalse(pet.bubble.isVisible())

    def test_bubble_position_stable_when_content_changes(self):
        self.window.move(50, 50)
        self.window.toggle_collapsed()
        pet = self.window.restore_button
        pet.bubble.present(pet, '<qt>작업 중</qt>')
        position = pet.bubble.pos()
        side = pet.bubble.on_right
        pet.bubble.present(pet, '<qt>' + '<br>'.join(['작업 중'] * 6) + '</qt>')
        self.assertEqual(pet.bubble.pos(), position)
        pet.bubble.present(pet)
        self.assertEqual(pet.bubble.pos(), position)
        self.assertEqual(pet.bubble.on_right, side)

    def test_agent_notification_is_sent_once(self):
        task = dict(id='x', agent='codex', status='running', label='작업 중', started=1, updated=2, target='x')
        with patch.object(self.window, 'send_notification') as notify:
            self.window.received_activity([task])
            task = dict(task, status='done', label='작업 완료', updated=3)
            self.window.received_activity([task])
            self.window.received_activity([task])
            notify.assert_called_once()

    def test_theme_click_persists_and_preserves_quota(self):
        QTest.mouseClick(self.window.theme_buttons["light"], Qt.LeftButton)
        self.assertEqual(self.settings.value("theme"), "light")
        self.assertEqual(self.window.cards["codex"].amount.text(), "43%")
        for mode in ("dark", "light"):
            QTest.mouseClick(self.window.theme_buttons[mode], Qt.LeftButton)
            self.app.processEvents()
            image = self.window.grab().toImage()
            self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
            self.assertEqual(image.pixelColor(40, 40).alpha(), 255)
            self.assertTrue(image.save(f"/tmp/ai-quota-widget-{mode}.png"))
        restored = Widget()
        self.assertEqual(restored.theme, "light")
        restored.close()
        restored.deleteLater()

    def test_home_menu_opens_apps_and_startup_always_shows_icons(self):
        QTest.mouseClick(self.window.home_button, Qt.LeftButton)
        self.assertTrue(self.window.dock.isVisible())
        self.assertFalse(self.window.scroll.isVisible())
        self.assertFalse(self.window.calendar.isVisible())
        self.assertFalse(self.window.page_heading.isVisible())
        self.assertFalse(self.window.home_button.isVisible())
        self.window.calendar.listing.setText("테스트 일정")
        self.window.refresh.reset_mock()
        QTest.mouseClick(self.window.page_buttons["calendar"], Qt.LeftButton)
        self.assertTrue(self.window.calendar.isVisible())
        self.assertFalse(self.window.scroll.isVisible())
        self.assertFalse(self.window.refresh_button.isVisible())
        self.assertEqual(self.window.page_heading.text(), "일정")
        self.assertFalse(self.window.dock.isVisible())
        self.assertTrue(self.window.home_button.isVisible())
        self.window.refresh.assert_not_called()
        self.settings.setValue("page", "calendar")
        restored = Widget()
        self.assertEqual(restored.pages.currentIndex(), 2)
        restored.close()
        restored.deleteLater()
        for mode in ("light", "dark"):
            self.window.set_theme(mode)
            self.window.resize(360, 580)
            self.app.processEvents()
            self.assertEqual(self.window.width(), 360)
            self.assertEqual(self.window.calendar_scroll.horizontalScrollBar().maximum(), 0)
            self.assertTrue(self.window.page_buttons["calendar"].isChecked())
        QTest.mouseClick(self.window.home_button, Qt.LeftButton)
        self.assertTrue(self.window.dock.isVisible())
        self.assertFalse(self.window.calendar.isVisible())
        QTest.mouseClick(self.window.page_buttons["quota"], Qt.LeftButton)
        self.assertFalse(self.window.dock.isVisible())
        self.assertEqual(self.window.cards["codex"].amount.text(), "43%")
        self.assertEqual(self.window.calendar.listing.text(), "테스트 일정")
        self.assertFalse(self.window.calendar.isVisible())
        self.assertTrue(self.window.refresh_button.isVisible())

    def test_long_calendar_list_scrolls_above_fixed_navigation(self):
        self.window.set_page("calendar")
        self.window.resize(360, 580)
        self.window.calendar.listing.setText("\n\n".join(f"{i}:00 · 일정 제목" for i in range(24)))
        for _ in range(3):
            self.app.processEvents()
        self.assertGreater(self.window.calendar_scroll.verticalScrollBar().maximum(), 0)
        self.assertLess(self.window.calendar.listing.geometry().bottom(), self.window.calendar.add_button.y())
        self.assertFalse(self.window.dock.isVisible())
        self.assertTrue(self.window.home_button.isVisible())
        self.assertEqual(self.window.width(), 360)

    def test_gpt_logo_launches_chatgpt_and_reports_failure(self):
        logo = self.window.cards["codex"].findChild(QPushButton, "badge")
        self.assertEqual(logo.accessibleName(), "ChatGPT 프로그램 열기")
        with patch("widget.QProcess.startDetached", return_value=(True, 123)) as launch:
            QTest.mouseClick(logo, Qt.LeftButton)
            launch.assert_called_once_with("chatgpt", [])
        with patch("widget.QProcess.startDetached", return_value=(False, 0)), \
                patch("widget.QMessageBox.warning") as warning:
            QTest.keyClick(logo, Qt.Key_Space)
            warning.assert_called_once()

    def test_web_app_shortcuts_open_browser_and_keep_home(self):
        self.window.set_page("home")
        self.window.resize(360, 580)
        self.app.processEvents()
        self.assertEqual(set(self.window.page_buttons), {"quota", "calendar", "github"})
        with patch("widget.QDesktopServices.openUrl", return_value=True) as open_url:
            button = self.window.page_buttons["github"]
            self.assertTrue(button.isVisible())
            self.assertTrue(self.window.dock.rect().contains(button.geometry()))
            QTest.mouseClick(button, Qt.LeftButton)
            self.assertEqual(open_url.call_args.args[0].toString(), "https://github.com/")
            self.assertEqual(self.window.pages.currentIndex(), 2)
            self.assertEqual(self.window.width(), 360)
            self.assertEqual(self.window.height(), 580)
        with patch("widget.QDesktopServices.openUrl", return_value=False), \
                patch("widget.QMessageBox.warning") as warning:
            QTest.mouseClick(self.window.page_buttons["github"], Qt.LeftButton)
            warning.assert_called_once()

    def test_app_icons_motion_interrupt_keyboard_and_reduce_motion(self):
        self.window.set_page("home")
        calendar = self.window.page_buttons["calendar"]
        quota = self.window.page_buttons["quota"]
        self.assertEqual(calendar.size(), quota.size())
        QTest.mousePress(calendar, Qt.LeftButton)
        QTest.qWait(80)
        self.assertLess(calendar.scale, 1.0)
        QTest.mouseRelease(calendar, Qt.LeftButton)
        self.assertLess(self.window.page_opacity.opacity(), 1.0)
        QTest.mouseClick(self.window.home_button, Qt.LeftButton)
        QTest.mouseClick(quota, Qt.LeftButton)
        QTest.qWait(250)
        self.assertEqual(self.window.pages.currentIndex(), 0)
        self.assertEqual(self.window.page_opacity.opacity(), 1.0)
        self.assertEqual(calendar.scale, 1.0)
        QTest.mouseClick(self.window.home_button, Qt.LeftButton)
        calendar.setFocus()
        QTest.keyClick(calendar, Qt.Key_Space)
        self.assertEqual(self.window.pages.currentIndex(), 1)
        self.assertEqual(self.window.page_opacity.opacity(), 1.0)
        self.window.reduce_motion.setChecked(True)
        QTest.mouseClick(self.window.home_button, Qt.LeftButton)
        QTest.mousePress(quota, Qt.LeftButton)
        QTest.qWait(50)
        self.assertEqual(quota.scale, 1.0)
        QTest.mouseRelease(quota, Qt.LeftButton)
        self.assertEqual(self.window.page_opacity.opacity(), 1.0)
        self.assertTrue(self.settings.value("reduce_motion", type=bool))

    def test_clock_rolls_over_without_fetching_and_uses_updated_branding(self):
        self.assertEqual(self.window.windowTitle(), "check AI")
        self.assertFalse(any(item.text() == "check AI" for item in self.window.findChildren(QLabel)))
        self.assertTrue(self.window.note.isHidden())
        self.assertEqual(self.window.clock.font().pixelSize(), 20)
        self.assertTrue(self.window.clock_timer.isActive())
        self.assertEqual(self.window.clock_timer.interval(), 1000)
        with patch("widget.datetime") as clock:
            clock.now.return_value = datetime(2026, 9, 11, 23, 59, 59)
            self.window.update_clock()
            self.assertEqual(self.window.clock.text(), "2026.09.11 23:59:59")
            clock.now.return_value = datetime(2026, 9, 12, 0, 0, 0)
            self.window.refresh.reset_mock()
            self.window.clock_timer.setInterval(10)
            QTest.qWait(50)
            self.assertEqual(self.window.clock.text(), "2026.09.12 00:00:00")
            self.window.refresh.assert_not_called()
        for mode in ("dark", "light"):
            self.window.set_theme(mode)
            self.app.processEvents()
            card = self.window.cards["codex"]
            self.assertEqual(card.amount.font().pixelSize(), 26)
            self.assertEqual(card.details.findChild(QLabel, "rowValue").font().pixelSize(), 10)
        self.window.close()
        self.assertFalse(self.window.clock_timer.isActive())

    def test_resize_and_geometry_restore(self):
        self.window.move(0, 0)
        self.window.resize(520, 650)
        self.app.processEvents()
        self.assertEqual(self.window.resize_edges(QPointF(1, 1)), Qt.TopEdge | Qt.LeftEdge)
        self.assertFalse(self.window.resize_edges(QPointF(100, 100)))
        self.assertTrue(self.window.grip.isVisible())
        start = self.window.size()
        QTest.mousePress(self.window.grip, Qt.LeftButton, pos=QPoint(8, 8))
        QTest.mouseMove(self.window.grip, QPoint(48, 38))
        QTest.mouseRelease(self.window.grip, Qt.LeftButton, pos=QPoint(48, 38))
        self.assertGreater(self.window.width(), start.width())
        self.assertGreater(self.window.height(), start.height())
        self.window.close()
        restored = Widget()
        self.assertEqual(restored.height(), 440)
        restored.set_page("quota")
        self.assertEqual(restored.size(), self.window.size())
        restored.close()
        restored.deleteLater()

    def test_small_window_and_expanded_details_remain_scrollable(self):
        self.window.resize(360, 580)
        for card in self.window.cards.values():
            QTest.mouseClick(card.details_button, Qt.LeftButton)
        self.app.processEvents()
        self.assertTrue(self.window.cards["codex"].details.isVisible())
        self.assertGreater(self.window.scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(self.window.scroll.horizontalScrollBar().maximum(), 0)
        self.assertTrue(self.window.grip.isVisible())
        self.window.grab().save("/tmp/ai-quota-widget-small.png")

    def test_failed_refresh_keeps_last_snapshot_and_visible_warning(self):
        card = self.window.cards["codex"]
        before = card.last_success
        card.update_result(None, "네트워크 확인 필요")
        self.assertEqual(card.amount.text(), "43%")
        self.assertEqual(card.last_success, before)
        self.assertTrue(card.status.isVisible())
        QTest.mouseClick(self.window.theme_buttons["light"], Qt.LeftButton)
        self.assertTrue(card.status.isVisible())
        card.update_result(ROWS["codex"], "")
        self.assertTrue(card.status.isHidden())

    def test_transparency_slider_applies_clamps_and_persists(self):
        self.window.opacity_slider.setValue(40)
        self.assertAlmostEqual(self.window.windowOpacity(), 0.6, delta=0.01)
        self.assertEqual(self.settings.value("transparency"), 40)
        restored = Widget()
        self.assertEqual(restored.opacity_slider.value(), 40)
        self.assertAlmostEqual(restored.windowOpacity(), 0.6, delta=0.01)
        restored.close()
        restored.deleteLater()
        self.window.set_transparency(100)
        self.assertAlmostEqual(self.window.windowOpacity(), 0.3, delta=0.01)
        self.window.set_transparency(-1)
        self.assertEqual(self.window.windowOpacity(), 1)

    def test_second_launch_shows_existing_hidden_window_and_recovers_position(self):
        socket_path = Path(self.directory.name) / "widget.sock"
        server = listen_for_activation(self.window, socket_path)
        try:
            self.window.move(-10000, -10000)
            self.window.hide()
            self.assertTrue(activate_existing(socket_path))
            QTest.qWait(50)
            self.assertTrue(self.window.isVisible())
            self.assertTrue(self.app.primaryScreen().availableGeometry().contains(self.window.geometry()))
            self.assertFalse(activate_existing(Path(self.directory.name) / "missing.sock"))
        finally:
            server.close()

    def test_plan_labels_preserve_raw_names_and_mark_manual_values(self):
        self.assertEqual(self.window.cards["codex"].plan.text(), "플랜 · prolite")
        self.assertEqual(self.window.cards["copilot"].plan.text(), "플랜 · Copilot Pro")
        self.assertEqual(self.window.cards["antigravity"].plan.text(), "플랜 정보 미제공")
        with patch("widget.QInputDialog.getText", return_value=("Google AI Pro", True)):
            self.window.edit_antigravity_plan()
        self.assertIn("Google AI Pro (직접 설정)", self.window.cards["antigravity"].plan.text())
        self.window.received("copilot", None, "연결 실패")
        self.assertIn("Copilot Pro", self.window.cards["copilot"].plan.text())
        self.window.received("copilot", {"rows": ROWS["copilot"], "plan": None}, "")
        self.assertEqual(self.window.cards["copilot"].plan.text(), "플랜 확인 안 됨")


if __name__ == "__main__":
    unittest.main()
