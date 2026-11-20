"""Run with QT_QPA_PLATFORM=offscreen. No network or real settings are touched."""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings, QPoint, QPointF, QSize, QEvent, QObject, QAbstractAnimation, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QMenu, QProgressBar

from widget import THEMES, Card, Widget, activate_existing, listen_for_activation, risk_level, time_left
from accounts import logo_asset
from pixel_pet import (DURATIONS, FIDGETS, GLANCE_L, GLANCE_R, HOP, HOVER_R, HOVER, WORK_BREAKS, WORK_LEAVE,
                       animation_row, display_row)


ROWS = {
    "codex": [{"label": "Codex · 주간", "remaining": 43, "reset": "09/15 10:28 초기화"},
              {"label": "GPT-5.3-Codex-Spark · 5시간", "remaining": 100, "reset": "09/11 13:20 초기화"}],
    "antigravity": [{"label": "Gemini · 주간", "remaining": 93, "reset": "09/11 09:02 초기화"},
                    {"label": "Claude and GPT · 5시간", "remaining": 100, "reset": "09/11 13:20 초기화"}],
    "copilot": [{"label": "채팅", "remaining": None, "reset": ""},
                {"label": "프리미엄", "remaining": 100, "detail": "200/200 크레딧", "reset": "10/01 초기화 · 시각 미제공"}],
    "claude": [{"label": "현재 세션", "remaining": 99, "reset": "Sep 28, 1pm (Asia/Seoul) 초기화"},
               {"label": "이번 주 · 전체 모델", "remaining": 67, "reset": "Sep 30, 3pm (Asia/Seoul) 초기화"}],
}

# Registered accounts named like the former fixed cards; claude_max is a second, isolated Claude login.
ACCOUNTS = [
    {"id": "codex", "kind": "codex", "label": "", "profile_dir": "", "use_default": True},
    {"id": "claude", "kind": "claude", "label": "", "profile_dir": "", "use_default": True},
    {"id": "claude_max", "kind": "claude", "label": "Claude · Max 5x", "profile_dir": "/nonexistent/max", "use_default": False},
    {"id": "antigravity", "kind": "antigravity", "label": "", "profile_dir": "", "use_default": True},
    {"id": "copilot", "kind": "copilot", "label": "", "profile_dir": "", "use_default": True},
]


def seed_accounts(settings, accounts=ACCOUNTS):
    settings.setValue("accounts", json.dumps(accounts))


class LifecycleTests(unittest.TestCase):
    def test_close_exits_and_next_launch_starts_all_refreshes(self):
        code = textwrap.dedent("""
            import sys
            from pathlib import Path
            from unittest.mock import patch
            from PySide6.QtCore import QSettings, QTimer
            from PySide6.QtWidgets import QApplication
            import widget
            from desktop import DesktopWindow
            desktop, pet, directory = sys.argv[1:]
            kind = DesktopWindow if desktop == "1" else widget.Widget
            original = widget.Widget
            settings = QSettings(str(Path(directory) / "settings.ini"), QSettings.IniFormat)
            state = {"closed": False, "timeout": False, "error": ""}
            def create(*args, **kwargs):
                window = kind(*args, **kwargs)
                app = QApplication.instance()
                app.lastWindowClosed.connect(lambda: state.update(closed=True))
                def exercise():
                    try:
                        window.reduce_motion.setChecked(True)
                        for _ in range(int(pet)):
                            window.toggle_collapsed()
                        assert all(timer.isActive() for timer in (
                            window.timer, window.clock_timer, window.activity_timer,
                            window.calendar.timer, window.calendar.day_timer))
                        window.calendar.job = object()
                        assert not window.close(), "must protect pending calendar writes"
                        assert window.isVisible() and window.timer.isActive()
                        waited = []
                        window.calendar.job = type("Refresh", (), {"read_only": True, "wait": lambda self: waited.append(1)})()
                        assert window.close(), "a read-only refresh must not block closing"
                        assert waited, "closing must wait for the running refresh"
                    except Exception as error:
                        state["error"] = repr(error)
                        app.quit()
                def timeout():
                    state["timeout"] = True
                    app.quit()
                QTimer.singleShot(20, exercise)
                QTimer.singleShot(500, timeout)
                return window
            sys.argv = ["widget.py"] + (["--desktop"] if desktop == "1" else [])
            with patch("widget.QSettings", return_value=settings), \
                 patch.object(original, "refresh"), patch.object(original, "refresh_activity"), \
                 patch("calendar_panel.CalendarPanel.refresh"), \
                 patch("desktop.DesktopWindow" if desktop == "1" else "widget.Widget", side_effect=create):
                result = widget.main()
            print(state)
            sys.exit(0 if state["closed"] and not state["timeout"] and not state["error"] and result == 0 else 1)
        """)
        with tempfile.TemporaryDirectory() as directory:
            environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_RUNTIME_DIR=directory, XDG_CONFIG_HOME=directory)
            for desktop in (False, True):
                for pet in (0, 1, 2):
                    with self.subTest(desktop=desktop, pet=pet):
                        for _ in range(2):
                            result = subprocess.run(
                                [sys.executable, "-E", "-c", code, str(int(desktop)), str(int(pet)), directory],
                                cwd=Path(__file__).parent, env=environment, capture_output=True, text=True, timeout=5)
                            self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-1800:])


INSTANCE = textwrap.dedent("""
    import sys, time
    from pathlib import Path
    from unittest.mock import patch
    from PySide6.QtCore import QSettings, QTimer
    import widget
    role, directory, mode, startup, work, close_on = sys.argv[1:]
    log = lambda *words: print(role, *words, flush=True)
    settings = QSettings(str(Path(directory) / (role + ".ini")), QSettings.IniFormat)
    settings.setValue("accounts", '[{"id": "codex", "kind": "codex", "label": "", "profile_dir": "", "use_default": true}]')
    original_init, original_bring, original_wait = widget.Widget.__init__, widget.Widget.bring_to_front, widget.Fetch.wait
    activations = []
    def init(self, *args, **kwargs):
        log("constructing")
        time.sleep(float(startup))
        original_init(self, *args, **kwargs)
        log("started", type(self).__name__)
        if close_on == "start":
            QTimer.singleShot(100, self.close)
    def bring(self):
        original_bring(self)
        activations.append(self)
        if close_on == "activation" and len(activations) == 2:
            QTimer.singleShot(0, self.close)
    def wait(self, *args):
        log("waiting", self.parent().isVisible())
        return original_wait(self, *args)
    def fetch(account):
        time.sleep(float(work))
        return {"rows": [{"label": "주간", "remaining": 50, "reset": ""}], "plan": None}
    sys.argv = ["widget.py"] + (["--desktop"] if mode == "desktop" else [])
    with patch("widget.QSettings", return_value=settings), patch.dict(widget.PROVIDERS, {"codex": fetch}, clear=True), \\
         patch.object(widget.Widget, "refresh_activity"), patch("calendar_panel.CalendarPanel.refresh"), \\
         patch.object(widget.Widget, "__init__", init), patch.object(widget.Widget, "bring_to_front", bring), \\
         patch.object(widget.Fetch, "wait", wait), \\
         patch("widget.QMessageBox.warning", side_effect=lambda *args: log("warning")):
        result = widget.main()
    log("exit", result)
""")


class InstanceTests(unittest.TestCase):
    def launch(self, directory, role, mode="compact", startup=0, work=0, close_on="never"):
        environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_RUNTIME_DIR=directory, XDG_CONFIG_HOME=directory)
        return subprocess.Popen([sys.executable, "-E", "-c", INSTANCE, role, directory, mode, str(startup), str(work), close_on],
                                cwd=Path(__file__).parent, env=environment, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True)

    def read_until(self, process, word):
        lines = []
        for line in process.stdout:
            lines.append(line.strip())
            if word in line:
                return lines
        self.fail(f"{word!r} not printed: {lines}")

    def finish(self, process):
        output, _ = process.communicate(timeout=40)
        return [line for line in output.split("\n") if line and " waiting " not in line]

    def test_launch_during_startup_and_shutdown_is_not_lost(self):
        with tempfile.TemporaryDirectory() as directory:
            first = self.launch(directory, "a", startup=1, work=2, close_on="activation")
            try:
                self.read_until(first, "constructing")
                # The first instance holds the lock but is not listening yet.
                second = self.launch(directory, "b")
                self.assertEqual(self.finish(second), ["b exit 0"])
                self.assertEqual(self.read_until(first, "waiting"), ["a started Widget", "a waiting False"])
                # Relaunch while the closed window still waits for its quota worker.
                third = self.launch(directory, "c", close_on="start")
                self.assertEqual(self.finish(first), ["a exit 0"])
                self.assertEqual(self.finish(third), ["c constructing", "c started Widget", "c exit 0"])
            finally:
                for process in (first, locals().get("second"), locals().get("third")):
                    if process and process.poll() is None:
                        process.kill()
                        process.communicate()

    def test_other_mode_replaces_running_instance(self):
        for running, requested, name in (("compact", "desktop", "DesktopWindow"), ("desktop", "compact", "Widget")):
            with self.subTest(running=running, requested=requested), tempfile.TemporaryDirectory() as directory:
                first = self.launch(directory, "a", running)
                second = None
                try:
                    self.read_until(first, "started")
                    second = self.launch(directory, "b", requested, close_on="start")
                    self.assertEqual(self.finish(first), ["a exit 0"])
                    self.assertEqual(self.finish(second), ["b constructing", "b started " + name, "b exit 0"])
                finally:
                    for process in (first, second):
                        if process and process.poll() is None:
                            process.kill()
                            process.communicate()


class QuotaHelperTests(unittest.TestCase):
    def test_time_left_formats(self):
        now = 1_000_000.0
        cases = [(30 * 3600 + 5 * 60, "1일 6시간 후"), (48 * 3600, "2일 후"), (3 * 3600, "3시간 후"),
                 (3 * 3600 + 20 * 60, "3시간 20분 후"), (45 * 60, "45분 후"), (20, "1분 후"),
                 (-5, "곧 초기화"), (0, "곧 초기화")]
        for seconds, expected in cases:
            self.assertEqual(time_left(now + seconds, now), expected, seconds)
        self.assertEqual(time_left(None, now), "")

    def test_risk_level_thresholds(self):
        self.assertEqual([risk_level(v) for v in (None, 100, 21, 20, 11, 10, 0)],
                         ["", "", "", "low", "low", "critical", "critical"])


class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.directory.name) / "settings.ini"), QSettings.IniFormat)
        seed_accounts(self.settings)
        config_patch = patch.dict(os.environ, {"XDG_CONFIG_HOME": self.directory.name})
        config_patch.start()
        self.addCleanup(config_patch.stop)
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

    def test_clock_tick_does_not_repaint_cards_and_window_keeps_its_shadow(self):
        # A live graphics effect on the surface re-blurred and repainted every card on each clock tick.
        painted = []

        class Spy(QObject):
            def eventFilter(self, item, event):
                if event.type() == QEvent.Paint:
                    painted.append(type(item))
                return False
        spy = Spy()
        self.app.installEventFilter(spy)
        self.addCleanup(self.app.removeEventFilter, spy)
        self.app.processEvents()
        painted.clear()
        self.window.clock.setText("2000.01.01 00:00:00")
        self.app.processEvents()
        self.assertIn(QLabel, painted)
        self.assertNotIn(Card, painted)
        surface = self.window.surface.geometry()
        for theme in ("dark", "light"):
            self.window.set_theme(theme)
            image = self.window.grab().toImage()
            self.assertGreater(image.pixelColor(surface.center().x(), surface.bottom() + 3).alpha(), 0, theme)
            self.assertEqual(image.pixelColor(1, 1).alpha(), 0, theme)

    def test_claude_usage_card_shows_cli_limits(self):
        self.assertIn('claude', self.window.cards)
        card = self.window.cards['claude']
        self.assertEqual(card.amount.text(), '67%')
        self.assertTrue(card.bar.isVisible())
        self.assertTrue(card.details_button.isVisible())
        self.assertIn('이번 주', card.summary_label.text())
        self.assertIn('Sep 30', card.reset.text())

    def test_weekly_limits_stay_first_across_services_and_refreshes(self):
        cases = [
            ("codex", "Codex · 주간", "GPT-5.3-Codex-Spark · 주간", "Codex · 5시간"),
            ("claude", "이번 주 · 전체 모델", "이번 주 · Fable", "현재 세션"),
            ("claude_max", "이번 주 · 전체 모델", "이번 주 · Fable", "현재 세션"),
            ("antigravity", "Gemini · 주간", "Claude and GPT · 주간", "Gemini · 5시간"),
        ]
        with patch.object(self.window.notifications, "quota") as notify:
            for key, weekly, model_weekly, session in cases:
                with self.subTest(service=key):
                    # Overall weekly quota wins even when a model/session has less left.
                    model_remaining = 90 if key == "antigravity" else 30
                    rows = [{"label": session, "remaining": 5, "reset": "오늘 초기화"},
                            {"label": model_weekly, "remaining": model_remaining, "reset": "다음 주 초기화"},
                            {"label": weekly, "remaining": 80, "reset": "주간 초기화"}]
                    for incoming in (rows, rows[::-1]):
                        original = list(incoming)
                        self.window.received(key, {"rows": incoming}, "")
                        card = self.window.cards[key]
                        self.assertEqual(card.amount.text(), "80%")
                        self.assertEqual(card.bar.value(), 800)
                        self.assertIn(weekly, card.summary_label.text())
                        self.assertNotIn("가장 적은", card.summary_label.text())
                        self.assertEqual(card.reset.text(), "주간 초기화")
                        titles = [card.rows.itemAt(i).widget().findChild(QLabel, "rowTitle").text()
                                  for i in range(card.rows.count())]
                        self.assertEqual(titles, [weekly, model_weekly, session])
                        self.assertEqual(self.window.pet_quotas[key], "80% 남음")
                        self.assertTrue(self.window.pet_low[key])
                        self.assertTrue(card.property("warning"))
                        self.assertEqual(incoming, original)
                        notify.assert_called_with(key, unittest.mock.ANY, incoming)

    def test_no_weekly_limit_keeps_remaining_and_unlimited_fallbacks(self):
        rows = [{"label": "채팅", "remaining": None, "reset": ""},
                {"label": "프리미엄", "remaining": 25, "reset": "월간 초기화"}]
        self.window.received("copilot", {"rows": rows}, "")
        card = self.window.cards["copilot"]
        self.assertEqual(card.amount.text(), "25%")
        self.assertEqual(card.reset.text(), "월간 초기화")
        self.assertEqual(self.window.pet_quotas["copilot"], "25% 남음")
        self.window.received("copilot", {"rows": rows[:1]}, "")
        self.assertEqual(card.amount.text(), "무제한")
        self.assertTrue(card.bar.isHidden())

    def test_two_claude_accounts_keep_independent_limits_and_login(self):
        self.assertIn('claude_max', self.window.cards)
        business = self.window.cards['claude']
        personal = self.window.cards['claude_max']
        self.window.received('claude', {'rows': ROWS['claude'], 'plan': 'team', 'account': 'work@example.test'}, '')
        self.window.received('claude_max', {'rows': [{'label': '현재 세션', 'remaining': 25, 'reset': '1pm 초기화'}],
                                           'plan': 'max', 'account': 'personal@example.test'}, '')
        self.assertEqual(business.amount.text(), '67%')
        self.assertEqual(personal.amount.text(), '25%')
        self.assertIn('work@example.test', business.plan.text())
        self.assertIn('personal@example.test', personal.plan.text())
        self.assertEqual(personal.plan_badge.text(), 'Max')
        self.assertEqual(personal.findChild(QLabel, 'serviceTitle').text(), 'Claude · Max 5x')
        self.assertEqual(business.findChild(QLabel, 'serviceTitle').text(), 'Claude Code')
        # New logins start from "+ 추가"; a card only logs in again to its own account or is removed.
        self.assertEqual([action.text() for action in personal.menu_button.menu().actions()], ['다시 로그인', '계정 제거…'])

    def test_wayland_uses_positionable_backend_without_overriding_explicit_choice(self):
        import os
        import subprocess
        import sys
        if not sys.platform.startswith("linux"):
            self.skipTest("Linux desktop backend selection")
        for display, wayland, explicit, expected in (
            (":0", "wayland-0", "", "xcb"),
            (":0", "wayland-0", "offscreen", "offscreen"),
            (":0", "", "", ""),
            ("", "wayland-0", "", ""),
        ):
            with self.subTest(display=display, wayland=wayland, explicit=explicit):
                environment = dict(os.environ)
                for key, value in (("DISPLAY", display), ("WAYLAND_DISPLAY", wayland), ("QT_QPA_PLATFORM", explicit)):
                    environment.pop(key, None)
                    if value:
                        environment[key] = value
                result = subprocess.run(
                    [sys.executable, "-c", "import os, widget; print(os.environ.get('QT_QPA_PLATFORM', ''))"],
                    cwd=Path(__file__).parent, env=environment, capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)

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
        self.window.bring_to_front()
        self.assertFalse(self.window.collapsed)
        self.assertEqual(self.window.geometry(), original)
        self.window.toggle_collapsed()
        self.window.close()
        self.assertEqual(self.settings.value("geometry"), self.window.expanded_geometry)

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

    def test_size_preset_on_home_sets_feature_size_and_stays_on_screen(self):
        area = self.window.screen().availableGeometry()
        self.window.set_page("home")
        self.window.move(area.right() - 200, area.top())
        sizes = next(menu for menu in self.window.menu.findChildren(QMenu) if menu.title() == "위젯 크기")
        next(action for action in sizes.actions() if action.text() == "크게").trigger()
        self.assertEqual(self.window.height(), 440)
        self.window.set_page("quota")
        self.assertEqual(self.window.size(), QSize(520, min(900, area.height() - 20)))
        self.assertTrue(area.contains(self.window.geometry()))

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

    def test_double_click_on_pet_does_not_reach_revealed_window(self):
        self.window.set_theme("dark")
        self.window.toggle_collapsed()
        self.window.fold_transition.finish()
        center = self.window.restore_button.geometry().center()
        # Window-level clicks are routed by position like real pointer input.
        QTest.mouseClick(self.window.windowHandle(), Qt.LeftButton, Qt.NoModifier, center)
        self.assertFalse(self.window.collapsed)
        self.assertEqual(self.window.fold_transition.motion.state(), QAbstractAnimation.Running)
        QTest.mouseClick(self.window.windowHandle(), Qt.LeftButton, Qt.NoModifier, center)
        self.assertEqual(self.window.theme, "dark")
        self.window.fold_transition.finish()
        QTest.mouseClick(self.window.windowHandle(), Qt.LeftButton, Qt.NoModifier,
                         self.window.theme_buttons["light"].mapTo(self.window, QPoint(5, 5)))
        self.assertEqual(self.window.theme, "light")

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
        pet.life.rng.seed(3)
        with patch.object(pet, "underMouse", return_value=False):
            for row in (3, 5, 6, 7, 8):
                pet.set_status(row)
                for _ in range(10):  # transition rows (standing up, opening the laptop) lead into the state
                    if display_row(pet) == row:
                        break
                    pet.tick()
                for _ in range(3 * len(DURATIONS[row])):
                    self.assertEqual(display_row(pet), row)
                    pet.tick()
                for _ in range(10):  # then back to the base pose: standing up again, closing the laptop
                    if display_row(pet) in (0, GLANCE_L, GLANCE_R):
                        break
                    pet.tick()
                self.assertIn(display_row(pet), (0, GLANCE_L, GLANCE_R))
                self.assertEqual(pet.frame, 0)
                self.assertTrue(2500 * .88 <= pet.idle.interval() <= 7000 * 1.12)  # a still hold, not a fast loop
            with patch.object(pet, "underMouse", return_value=True):
                self.assertEqual(animation_row(pet), 4)
            self.window.reduce_motion.setChecked(True)
            pet.set_status(5)
            for _ in range(30):
                pet.tick()
            self.assertEqual(display_row(pet), 5)
            self.assertEqual(pet.frame, 0)

    def test_pet_works_with_breaks_follows_cursor_and_sleeps_when_hidden(self):
        from PySide6.QtGui import QMouseEvent
        self.window.toggle_collapsed()
        pet = self.window.restore_button
        pet.life.rng.seed(5)
        self.assertTrue(pet.idle.isSingleShot())
        with patch.object(pet, "underMouse", return_value=False), patch.object(self.window.calendar, "job", object()):
            pet.sync_animation()
            seen = set()
            while pet.life.clock < 120_000:
                pet.tick()
                seen.add(display_row(pet))
            self.assertIn(7, seen)  # typing keeps going while work runs, with breaks but no idle fidgets
            self.assertTrue(seen & set(WORK_BREAKS))
            self.assertFalse(seen & set(FIDGETS))
        with patch.object(pet, "underMouse", return_value=True):
            pet.sync_animation()
            rows = []
            for _ in range(30):
                rows.append(display_row(pet))
                pet.tick()
            self.assertEqual(rows[0], WORK_LEAVE)  # closes the laptop first
            self.assertIn(HOP, rows)  # one small happy hop
            self.assertEqual(rows[-1], HOVER[0])
            pet.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(85, 40), pet.mapToGlobal(QPointF(85, 40)),
                                           Qt.NoButton, Qt.NoButton, Qt.NoModifier))
            self.assertEqual(display_row(pet), HOVER_R)  # eyes turn toward the cursor
        self.assertTrue(pet.idle.isActive())
        pet.hide()
        self.assertFalse(pet.idle.isActive())
        pet.show()
        self.window.reduce_motion.setChecked(True)
        self.assertFalse(pet.idle.isActive())
        image = pet.grab().toImage()
        for _ in range(5):
            pet.tick()
        self.assertEqual(image, pet.grab().toImage())

    def test_pet_mode_slows_hidden_clock_and_reduced_motion_stops_frames(self):
        self.window.toggle_collapsed()
        pet = self.window.restore_button
        self.assertEqual(self.window.clock_timer.interval(), 60_000)
        self.assertEqual(self.window.timer.interval(), 180_000)
        self.assertEqual(self.window.activity_timer.interval(), 5_000)
        before = self.window.clock.text()
        with patch("widget.datetime") as clock:
            clock.now.return_value = datetime(2030, 1, 1, 12, 0, 0)
            self.window.update_clock()
        self.assertEqual(self.window.clock.text(), before)
        self.window.reduce_motion.setChecked(True)
        self.assertFalse(pet.idle.isActive())
        pet.play_animation(4)
        self.assertTrue(pet.idle.isActive())
        for _ in DURATIONS[4]:
            pet.tick()
        self.assertFalse(pet.idle.isActive())
        self.window.toggle_collapsed()
        self.assertEqual(self.window.clock_timer.interval(), 1_000)

    def test_transition_images_are_released_after_animation(self):
        from PySide6.QtGui import QPixmap
        self.window.page_opacity.snapshot = QPixmap(400, 600)
        self.window.page_opacity.setOpacity(1.0)
        self.assertTrue(self.window.page_opacity.snapshot.isNull())
        self.window.fold_transition.snapshot = QPixmap(400, 600)
        self.window.fold_transition.finish()
        self.assertTrue(self.window.fold_transition.snapshot.isNull())

    def test_agent_activity_bubble(self):
        self.window.toggle_collapsed()
        pet = self.window.restore_button
        with patch.object(pet, 'underMouse', return_value=False):
            tasks = [dict(id=key, agent=key, label=label, status='waiting' if key == 'copilot' else 'running', started=datetime.now().timestamp()-180, updated=2, target='/tmp/test')
                     for key, label in [('codex', '파일 수정 중'), ('antigravity', '<회의>'), ('copilot', '승인·입력 대기'), ('claude', '명령 실행 중')]]
            self.window.received_activity(tasks)
            self.assertTrue(pet.bubble.isVisible())
            rows = pet.bubble.rows
            self.assertEqual([row['kind'] for row in rows], ['codex', 'antigravity', 'copilot', 'claude'])
            self.assertTrue(all(logo_asset(row['kind'], True).exists() for row in rows))
            self.assertEqual(logo_asset('claude', True).name, 'claude-white.svg')
            # Painted as plain text: markup in a label is shown, never interpreted.
            self.assertEqual(rows[1]['current'], '<회의>')
            self.assertIn('<회의>', pet.bubble.accessibleDescription())
            self.assertTrue(pet.bubble.waiting)
            self.assertEqual(rows[2]['pill'], '승인 대기')
            self.assertTrue(pet.bubble.pulse_timer.isActive())
            self.assertIn('경과', rows[0]['elapsed'])
            self.assertFalse(pet.bubble.text.isVisible())
            with patch('widget.XWindows') as windows:
                windows.return_value.candidates.return_value = [(123, 'Terminator')]
                QTest.mouseClick(pet.bubble, Qt.LeftButton, pos=rows[1]['rect'].center().toPoint())
                windows.return_value.candidates.assert_called_once_with('antigravity', '/tmp/test')
                windows.return_value.activate.assert_called_once_with(123)
                windows.return_value.close.assert_called_once()
            with patch('widget.XWindows') as windows:
                windows.return_value.candidates.return_value = [(456, 'Claude terminal')]
                QTest.mouseClick(pet.bubble, Qt.LeftButton, pos=rows[3]['rect'].center().toPoint())
                windows.return_value.candidates.assert_called_once_with('claude', '/tmp/test')
                windows.return_value.activate.assert_called_once_with(456)
            self.window.reduce_motion.setChecked(True)
            self.assertFalse(pet.bubble.pulse_timer.isActive())
            self.window.reduce_motion.setChecked(False)
            for theme in ('light', 'dark'):
                self.window.set_theme(theme)
                pet.bubble.grab().save('/tmp/agent-bubble-' + theme + '.png')
            self.window.received_activity([])
            self.assertTrue(pet.bubble.isVisible())
            self.window.received_activity([dict(task, status='cancelled') for task in tasks])
            self.assertFalse(pet.bubble.isVisible())

    def test_bubble_shows_subagent_model_effort_and_can_hide_it(self):
        self.window.toggle_collapsed()
        pet = self.window.restore_button
        sub = {'type': 'implementer-hard', 'model': 'opus 5.5', 'effort': 'xhigh', 'status': 'running'}
        task = dict(id='s', agent='claude', label='서브에이전트 2개 작업 중', status='running', title='ADD',
                    started=datetime.now().timestamp() - 60, updated=2, target='/tmp/test', model='opus 5.5',
                    effort='medium', subagents=[sub, dict(sub, type='reviewer')])
        with patch.object(pet, 'underMouse', return_value=False):
            self.window.received_activity([task])
            row = pet.bubble.rows[0]
            self.assertEqual(row['pill'], '서브 2개')
            self.assertEqual(row['model'], 'opus 5.5 · medium')
            self.assertEqual(row['chips'], ['implementer-hard · opus 5.5 xhigh', 'reviewer · opus 5.5 xhigh'])
            self.assertFalse(pet.bubble.waiting)
            self.assertFalse(pet.bubble.pulse_timer.isActive())
            many = dict(task, subagents=[sub, sub, dict(sub, type='a'), dict(sub, type='b'), dict(sub, type='c'), dict(sub, type='c')])
            self.window.received_activity([many])
            self.assertEqual(pet.bubble.rows[0]['chips'], ['implementer-hard · opus 5.5 xhigh ×2', 'a · opus 5.5 xhigh',
                                                           'b · opus 5.5 xhigh', '+2'])
            self.window.settings.setValue("pet/show_models", False)
            pet.show_activity()
            text = pet.bubble.accessibleDescription()
            self.assertEqual(pet.bubble.rows[0]['chips'], [])
            self.assertNotIn('xhigh', text)
            self.assertNotIn('medium', text)
            self.window.settings.setValue("pet/show_models", True)

    def test_bubble_click_finds_task_by_id_after_list_changes(self):
        self.window.toggle_collapsed()
        pet = self.window.restore_button
        tasks = [dict(id=key, agent='claude', label='작업 중', status='running', started=1, updated=2, target='/tmp/' + key)
                 for key in ('a', 'b')]
        with patch.object(pet, 'underMouse', return_value=False):
            self.window.received_activity(tasks)
        first = pet.bubble.rows[0]['rect'].center().toPoint()
        # The list reorders before the click lands; the painted row still means session "a".
        self.window.agent_activity = list(reversed(self.window.agent_activity))
        with patch('widget.XWindows') as windows:
            windows.return_value.candidates.return_value = [(7, 'a')]
            QTest.mouseClick(pet.bubble, Qt.LeftButton, pos=first)
            windows.return_value.candidates.assert_called_once_with('claude', '/tmp/a')
        self.window.agent_activity = [tasks[1]]
        with patch('widget.XWindows') as windows:
            QTest.mouseClick(pet.bubble, Qt.LeftButton, pos=first)
            windows.assert_not_called()
        five = [dict(tasks[0], id=str(i)) for i in range(5)]
        with patch.object(pet, 'underMouse', return_value=False):
            self.window.received_activity(five)
        self.assertEqual(len(pet.bubble.rows), 4)
        self.assertIsNotNone(pet.bubble.footer)
        with patch.object(self.window, 'open_activity') as open_activity:
            QTest.mouseClick(pet.bubble, Qt.LeftButton, pos=pet.bubble.footer.center().toPoint())
            open_activity.assert_called_once_with(None)

    def test_bubble_opacity_setting_persists_and_fades_background_only(self):
        from PySide6.QtGui import QAction
        self.assertEqual(self.window.bubble_opacity, 100)
        self.window.toggle_collapsed()
        pet = self.window.restore_button
        with patch.object(pet, 'underMouse', return_value=False):
            self.window.received_activity([dict(id='x', agent='codex', label='작업 중', status='running',
                                                started=1, updated=2, target='/tmp/x')])
        bubble = pet.bubble
        spot = QPoint(180, 30)  # plain body between the header title and its counts
        self.assertEqual(bubble.grab().toImage().pixelColor(spot).alpha(), 255)
        self.window.bubble_opacity_slider.setValue(60)
        self.assertEqual(self.settings.value("pet/bubble_opacity"), 60)
        self.assertAlmostEqual(bubble.grab().toImage().pixelColor(spot).alpha(), 153, delta=3)
        restored = Widget()
        self.assertEqual(restored.bubble_opacity, 60)
        self.assertEqual(restored.bubble_opacity_slider.value(), 60)
        restored.close()
        restored.deleteLater()
        for value, expected in ((10, 30), (200, 100), ("bad", 100)):
            self.window.set_bubble_opacity(value)
            self.assertEqual(self.window.bubble_opacity, expected)
        seen = []

        class Menu(QMenu):
            def exec(self, *args):
                fade = [a for a in self.findChildren(QAction) if "%" in a.text()]
                seen.extend((a.text(), a.isChecked()) for a in fade)
                return next(a for a in fade if a.text() == "70%")
        with patch("widget.QMenu", Menu):
            pet.contextMenuEvent(type("Event", (), {"globalPos": lambda self: QPoint(0, 0)})())
        self.assertEqual(len(seen), 6)
        self.assertIn(("100% · 불투명", True), seen)
        self.assertEqual(self.window.bubble_opacity, 70)
        self.assertEqual(self.window.bubble_opacity_slider.value(), 70)
        self.assertEqual(self.settings.value("pet/bubble_opacity"), 70)

    def test_unknown_agents_never_raise_and_opencode_is_read(self):
        from widget import ActivityFetch, service_info, session_title
        from agent_windows import agent_pids
        self.assertEqual(service_info("gemini")[0], "gemini")
        self.assertEqual(service_info(None)[0], "에이전트")
        from agent_activity import claude_project
        home = claude_project(str(Path.home()))
        self.assertEqual(session_title({"agent": "claude", "target": f"/x/.claude/projects/{home}-my-app/s.jsonl"}), "my-app")
        self.assertEqual(session_title({"agent": "claude", "title": " 리뷰 ", "target": ""}), "리뷰")
        self.assertEqual(session_title({"agent": "codex", "target": "/tmp/r.jsonl"}), "GPT / Codex")
        with patch("widget.read_activity", return_value=[]) as read:
            ActivityFetch().run()
        read.assert_called_once_with(detailed=True, extra_agents=True)
        job = ActivityFetch()
        received = []
        job.result.connect(received.append)
        with patch("widget.read_activity", side_effect=TypeError("bad log")), patch("sys.stderr"):
            job.run()
        self.assertEqual(received, [])
        self.assertEqual(agent_pids("gemini", "/nonexistent"), set())
        self.window.toggle_collapsed()
        pet = self.window.restore_button
        now = datetime.now().timestamp()
        tasks = [dict(id="g", agent="gemini", label="작업 중", status="running", started=now - 30, updated=now, target="/tmp/g",
                      subagents=[{"type": None, "model": None}], todos=[{"text": "x"}]),
                 dict(id="o", agent="opencode", label="작업 중", status="waiting", started=now - 30, updated=now, target="/tmp/o")]
        with patch.object(pet, "underMouse", return_value=False), patch.object(Widget, "send_notification") as notify:
            self.window.received_activity(tasks)
            self.assertEqual([row['title'] for row in pet.bubble.rows], ["gemini", "opencode"])
            self.assertEqual(logo_asset("opencode", True).name, "opencode-white.svg")
            self.assertEqual(pet.bubble.rows[0]['chips'], ["agent"])
            self.window.received_activity([dict(task, status="done") for task in tasks])
        self.assertEqual([call.args[0] for call in notify.call_args_list],
                         ["gemini · 작업 완료", "opencode · 작업 완료"])
        self.window.agent_activity = tasks
        with patch("widget.QMenu") as menu:
            menu.return_value.exec.return_value = None
            self.window.open_activity(None)
        self.assertIn("gemini · 작업 중", [call.args[0] for call in menu.return_value.addAction.call_args_list])

    def test_neon_turn_on_flicker_respects_reduced_motion(self):
        signs = self.window.neon_signs
        self.assertEqual([sign.text() for sign in signs], ["check AI", "AI 한도"])
        self.window.neon_motion.start()
        self.window.neon_motion.setCurrentTime(150)
        self.assertLess(signs[0].lit, 1.0)
        self.window.reduce_motion.setChecked(True)
        self.assertNotEqual(self.window.neon_motion.state(), QAbstractAnimation.Running)
        self.assertEqual([sign.lit for sign in signs], [1.0, 1.0])
        reduced = Widget()
        reduced.show()
        self.assertNotEqual(reduced.neon_motion.state(), QAbstractAnimation.Running)
        reduced.close()
        reduced.deleteLater()

    def test_activity_bubble_reanchors_after_each_pet_restore(self):
        self.window.move(50, 50)
        pet = self.window.restore_button
        self.window.agent_activity = [dict(agent="codex", status="running", label="작업 중", elapsed=120, estimated=False)]
        with patch.object(pet, "underMouse", return_value=False):
            for _ in range(3):
                self.window.toggle_collapsed()
                self.app.processEvents()
                self.assertTrue(pet.bubble.isVisible())
                anchor = pet.mapToGlobal(pet.rect().center())
                self.assertEqual(pet.bubble.placement_anchor, anchor)
                self.assertEqual(pet.bubble.x(), anchor.x() + 54)
                self.window.toggle_collapsed()
                self.app.processEvents()

    def test_bubble_uses_screen_at_pet_center_with_offset_monitors(self):
        from PySide6.QtCore import QRect
        from unittest.mock import Mock
        self.window.toggle_collapsed()
        pet = self.window.restore_button
        stale_screen = Mock()
        stale_screen.availableGeometry.return_value = QRect(0, 280, 1920, 1200)
        for bounds, point in ((QRect(1920, 0, 1920, 1080), QPoint(2053, 73)),
                              (QRect(-1280, -240, 1280, 1024), QPoint(-1150, -170))):
            actual_screen = Mock()
            actual_screen.availableGeometry.return_value = bounds
            with self.subTest(bounds=bounds), patch.object(pet, "screen", return_value=stale_screen), \
                    patch.object(pet, "mapToGlobal", return_value=point), \
                    patch("widget.QApplication.screenAt", return_value=actual_screen):
                pet.bubble.present(pet)
                self.assertTrue(bounds.contains(pet.bubble.geometry()))
                self.assertEqual(pet.bubble.x(), point.x() + 54)
                self.assertEqual(pet.bubble.y(), point.y() - 40)

    def test_bubble_recovers_after_external_reposition(self):
        self.window.move(50, 50)
        self.window.toggle_collapsed()
        pet = self.window.restore_button
        pet.bubble.present(pet)
        expected = pet.bubble.pos()
        pet.bubble.move(expected + QPoint(0, 280))
        pet.bubble.present(pet)
        self.assertEqual(pet.bubble.pos(), expected)

    def test_visible_summary_bubble_follows_window_move(self):
        self.window.move(50, 50)
        self.window.toggle_collapsed()
        pet = self.window.restore_button
        with patch.object(pet, "underMouse", return_value=True):
            self.app.processEvents()
            pet.bubble.present(pet)
            summary = pet.bubble.text.text()
            self.window.move(100, 100)
            self.app.processEvents()
            point = pet.mapToGlobal(pet.rect().center())
            self.assertTrue(pet.bubble.isVisible())
            self.assertEqual(pet.bubble.text.text(), summary)
            self.assertEqual(pet.bubble.pos(), QPoint(point.x() + 54, point.y() - 40))

    def test_bubble_position_stable_when_content_changes(self):
        self.window.move(50, 50)
        self.window.toggle_collapsed()
        pet = self.window.restore_button
        task = dict(id='t', agent='codex', status='running', label='작업 중', elapsed=5)
        pet.bubble.present(pet, [task])
        position = pet.bubble.pos()
        side = pet.bubble.on_right
        pet.bubble.present(pet, [dict(task, id=str(i), todos=[{'text': 'x'}]) for i in range(6)])
        self.assertEqual(pet.bubble.pos(), position)
        pet.bubble.present(pet)
        self.assertEqual(pet.bubble.pos(), position)
        self.assertEqual(pet.bubble.on_right, side)

    def test_bubble_is_placed_before_it_becomes_visible(self):
        self.window.toggle_collapsed()
        bubble = self.window.restore_button.bubble
        shown = []

        class ShowPosition(QObject):
            def eventFilter(self, watched, event):
                if event.type() == QEvent.Show:
                    shown.append(watched.pos())
                return False

        spy = ShowPosition()
        bubble.installEventFilter(spy)
        for position in (QPoint(100, 100), QPoint(300, 260)):
            bubble.hide()
            self.window.move(position)
            self.app.processEvents()
            bubble.present(self.window.restore_button)
            self.assertEqual(shown[-1], bubble.pos())
            self.assertEqual(bubble.placement_anchor, self.window.restore_button.mapToGlobal(
                self.window.restore_button.rect().center()))

    def test_agent_notification_is_sent_once(self):
        task = dict(id='x', agent='codex', status='running', label='작업 중', started=1, updated=2, target='x')
        with patch.object(self.window, 'send_notification') as notify:
            self.window.received_activity([task])
            task = dict(task, status='done', label='작업 완료', updated=3)
            self.window.received_activity([task])
            self.window.received_activity([task])
            notify.assert_called_once()

    def test_waiting_notification_respects_setting(self):
        task = dict(id='w', agent='claude', status='running', label='명령 실행 중', started=1, updated=2, target='x')
        waiting = dict(task, status='waiting', label='승인·입력 대기', updated=3)
        self.assertTrue(self.window.waiting_action.isChecked())
        with patch.object(self.window, 'send_notification') as notify, \
             patch.object(self.window.restore_button, 'set_status') as status:
            self.window.received_activity([task])
            self.window.received_activity([waiting])
            notify.assert_called_once_with("Claude Code · 승인·입력 대기", "승인·입력 대기")
            status.assert_not_called()
            notify.reset_mock()
            self.window.waiting_action.setChecked(False)
            self.assertFalse(self.settings.value("notify/agents/waiting", True, type=bool))
            self.window.received_activity([task])
            self.window.received_activity([waiting])
            notify.assert_not_called()

    def test_done_and_failed_notifications_respect_setting(self):
        task = dict(id='d', agent='codex', status='running', label='작업 중', started=1, updated=2, target='x')
        self.assertTrue(self.window.done_action.isChecked())
        self.assertIn(self.window.done_action, self.window.alerts.actions())
        self.window.done_action.setChecked(False)
        self.assertFalse(self.settings.value("notify/agents/done", True, type=bool))
        with patch.object(self.window, 'send_notification') as notify, \
             patch.object(self.window.restore_button, 'set_status') as status:
            for final in ('done', 'failed'):
                self.window.received_activity([dict(task, id=final)])
                self.window.received_activity([dict(task, id=final, status=final, updated=3)])
            notify.assert_not_called()
            self.assertEqual([c.args[0] for c in status.call_args_list], [8, 5])
            self.window.done_action.setChecked(True)
            self.window.received_activity([dict(task, id='again')])
            self.window.received_activity([dict(task, id='again', status='failed', updated=3)])
            notify.assert_called_once_with("GPT / Codex · 작업 실패", "작업 중")

    def test_smoke_render_does_not_store_notification_state(self):
        before = self.settings.value("notification_state")
        smoke = Widget(smoke=True)
        smoke.received("codex", {"rows": [{"label": "Smoke · 주간", "remaining": 5, "reset": "10/06 14:00 초기화"}]}, "")
        self.assertEqual(self.settings.value("notification_state"), before)
        smoke.close()
        smoke.deleteLater()

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

    def test_todo_icon_opens_only_the_local_list_and_keeps_it_after_navigation(self):
        self.window.set_page("home")
        QTest.mouseClick(self.window.page_buttons["todo"], Qt.LeftButton)
        self.assertTrue(self.window.todo_scroll.isVisible())
        self.assertFalse(self.window.calendar.isVisible())
        self.assertFalse(self.window.dock.isVisible())
        self.window.todo.input.setText("보고서 작성")
        QTest.keyClick(self.window.todo.input, Qt.Key_Return)
        self.window.set_page("home")
        self.window.set_page("todo")
        self.assertEqual(self.window.todo.items[0]["text"], "보고서 작성")

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
        self.assertEqual(set(self.window.page_buttons), {"quota", "calendar", "todo", "github"})
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

    def test_risk_colors_on_bars_and_amounts(self):
        self.window.set_theme("light")
        colors = THEMES["light"]
        card = self.window.cards["codex"]
        card.details_button.setChecked(True)
        cases = [(7, "critical", colors["danger"]), (15, "low", colors["warning"]), (50, "", colors["codex"])]
        for remaining, risk, color in cases:
            card.update_result([{"label": "Codex · 주간", "remaining": remaining, "reset": "10/04 02:11 초기화"},
                                {"label": "Codex · 5시간", "remaining": 80, "reset": "10/03 02:11 초기화"}], "")
            self.app.processEvents()
            self.assertEqual(card.amount.property("risk"), risk)
            self.assertEqual(card.bar.property("risk"), risk)
            self.assertEqual(card.bar.grab().toImage().pixelColor(8, 3), QColor(color), remaining)
            first, second = (card.rows.itemAt(i).widget() for i in range(2))
            value, detail = first.findChild(QLabel, "rowValue"), first.findChild(QProgressBar)
            self.assertEqual(value.property("risk"), risk)
            self.assertEqual(detail.property("risk"), risk)
            self.assertEqual(detail.grab().toImage().pixelColor(8, 2), QColor(color), remaining)
            self.assertEqual(second.findChild(QProgressBar).property("risk"), "")

    def test_card_countdown_updates_each_minute(self):
        card = Card(ACCOUNTS[0])
        reset_at = time.time() + 2 * 3600
        card.update_result([{"label": "Codex · 주간", "remaining": 50, "detail": "한도 안내",
                             "reset": "10/04 02:11 초기화", "reset_at": reset_at},
                            {"label": "Codex · 5시간", "remaining": 80, "reset": "10/03 02:11 초기화",
                             "reset_at": reset_at + 3600}], "")
        self.assertEqual(card.reset.text(), "한도 안내 · 10/04 02:11 초기화 · 2시간 후")
        card.refresh_countdown(reset_at - 59 * 60)
        self.assertEqual(card.reset.text(), "한도 안내 · 10/04 02:11 초기화 · 59분 후")
        captions = [item.text() for item in card.details.findChildren(QLabel, "caption")]
        self.assertEqual(captions[1], "10/03 02:11 초기화 · 1시간 59분 후")
        self.assertEqual(card.data[0]["reset"], "10/04 02:11 초기화")
        card.deleteLater()

    def test_clock_refreshes_countdowns_once_per_minute_even_when_collapsed(self):
        with patch.object(self.window, "update_countdowns") as update, patch("widget.datetime") as clock:
            for second, calls in ((5, 1), (30, 1), (59, 1)):
                clock.now.return_value = datetime(2026, 9, 11, 10, 0, second)
                self.window.update_clock()
                self.assertEqual(update.call_count, calls)
            self.window.toggle_collapsed()
            clock.now.return_value = datetime(2026, 9, 11, 10, 1, 0)
            self.window.update_clock()
            self.assertEqual(update.call_count, 2)
        with patch.object(Card, "refresh_countdown") as refresh:
            self.window.update_countdowns()
            self.assertEqual(refresh.call_count, len(self.window.cards))

    def test_quota_list_view_rows_and_persistence(self):
        window = self.window
        self.assertTrue(window.quota_list.isHidden())
        self.assertFalse(window.scroll.isHidden())
        self.assertEqual([b.accessibleName() for b in window.quota_view_switch.findChildren(QPushButton)],
                         ["카드 보기", "목록 보기"])
        window.received("antigravity", {"rows": [{"label": "Gemini · 주간", "remaining": 7, "reset": "09/11 09:02 초기화",
                                                  "reset_at": time.time() + 2 * 3600}]}, "")
        QTest.mouseClick(window.quota_view_buttons["list"], Qt.LeftButton)
        self.app.processEvents()
        self.assertFalse(window.quota_list.isHidden())
        self.assertTrue(window.scroll.isHidden())
        self.assertTrue(window.quota_view_buttons["list"].isChecked())
        self.assertEqual(self.settings.value("quota_view"), "list")
        self.assertEqual(list(window.quota_rows), [account["id"] for account in ACCOUNTS])
        codex = window.quota_rows["codex"]
        self.assertEqual(codex.amount.text(), "43%")
        self.assertEqual(codex.amount.property("risk"), "")
        self.assertTrue(codex.caption.text().startswith("Codex · 주간 · "))
        self.assertIn("GPT-5.3-Codex-Spark · 5시간: 100% 남음", codex.toolTip())
        anti = window.quota_rows["antigravity"]
        self.assertEqual((anti.amount.text(), anti.amount.property("risk"), anti.bar.property("risk")), ("7%", "critical", "critical"))
        self.assertEqual(anti.caption.text(), "Gemini · 주간 · 2시간 후")
        self.assertEqual(anti.bar.height(), 4)
        window.received("copilot", {"rows": [{"label": "채팅", "remaining": None, "reset": "", "reset_at": None}]}, "")
        self.assertEqual(window.quota_rows["copilot"].amount.text(), "무제한")
        self.assertEqual(window.quota_rows["copilot"].amount.property("risk"), "")
        window.received("claude_max", None, "로그인 확인 필요")
        failed = window.quota_rows["claude_max"]
        self.assertEqual((failed.amount.text(), failed.amount.property("risk")), ("조회 실패", "low"))
        self.assertIn("로그인 확인 필요", failed.toolTip())
        with patch("widget.time.time", return_value=time.time() + 61 * 60):
            window.update_countdowns()
        self.assertEqual(anti.caption.text(), "Gemini · 주간 · 59분 후")
        restored = Widget()
        self.assertFalse(restored.quota_list.isHidden())
        self.assertTrue(restored.scroll.isHidden())
        self.assertTrue(restored.quota_view_buttons["list"].isChecked())
        self.assertEqual(restored.quota_rows["codex"].amount.text(), "—")
        restored.received("claude", None, "로그인 필요")
        self.assertEqual(restored.quota_rows["claude"].amount.text(), "조회 실패")
        self.assertEqual(restored.settings.value("quota_view"), "list")
        restored.close()
        restored.deleteLater()
        smoke = Widget(smoke=True)
        smoke.set_quota_view("cards")
        self.assertEqual(self.settings.value("quota_view"), "list")
        smoke.close()
        smoke.deleteLater()

    def test_quota_list_row_click_opens_card(self):
        window = self.window
        window.set_quota_view("list")
        self.app.processEvents()
        row = window.quota_rows["copilot"]
        self.assertNotEqual(row.focusPolicy(), Qt.NoFocus)
        QTest.mouseClick(row, Qt.LeftButton)
        QTest.qWait(30)
        self.assertEqual(self.settings.value("quota_view"), "cards")
        self.assertTrue(window.quota_list.isHidden())
        self.assertFalse(window.scroll.isHidden())
        self.assertTrue(window.quota_view_buttons["cards"].isChecked())
        self.assertGreater(window.scroll.verticalScrollBar().value(), 0)
        card = window.cards["copilot"]
        self.assertTrue(window.scroll.viewport().rect().contains(card.mapTo(window.scroll.viewport(), card.rect().center())))
        for key in (Qt.Key_Return, Qt.Key_Space):
            window.set_quota_view("list")
            self.app.processEvents()
            window.quota_rows["codex"].setFocus()
            QTest.keyClick(window.quota_rows["codex"], key)
            QTest.qWait(30)
            self.assertTrue(window.quota_list.isHidden(), key)
            self.assertTrue(window.scroll.viewport().rect().contains(
                window.cards["codex"].mapTo(window.scroll.viewport(), window.cards["codex"].rect().center())))

    def test_list_fits_default_size(self):
        window = self.window
        for key in window.cards:
            window.received(key, {"rows": [{"label": "이번 주 · 전체 모델 · 아주 긴 한도 이름", "remaining": 9, "detail": "자세한 한도 안내 문구",
                                             "reset": "Oct 7, 3pm (Asia/Seoul) 초기화", "reset_at": None}]}, "")
        window.resize(430, 780)
        window.set_quota_view("list")
        self.app.processEvents()
        self.assertTrue(window.quota_list.isVisible())
        area = window.quota_list.rect()
        for key, row in window.quota_rows.items():
            self.assertTrue(row.isVisible(), key)
            bottom = row.mapTo(window.quota_list, row.rect().bottomLeft()).y()
            self.assertLessEqual(bottom, area.bottom(), key)
        self.assertEqual(window.scroll.verticalScrollBar().maximum() if window.scroll.isVisible() else 0, 0)

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

    def test_geometry_is_saved_after_move_without_close(self):
        self.window.move(40, 50)
        self.window.resize(500, 640)
        QTest.qWait(1100)
        restored = Widget()
        restored.set_page("quota")
        self.assertEqual(restored.geometry(), self.window.geometry())
        restored.deleteLater()
        self.window.toggle_collapsed()
        self.window.move(300, 300)
        QTest.qWait(1100)
        self.assertEqual(self.settings.value("geometry"), self.window.expanded_geometry)
        self.assertEqual(self.settings.value("feature_size"), QSize(500, 640))

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

    def activate_from_process(self, socket_path, mode="compact"):
        # The client blocks for the reply, so it must run outside this event loop.
        code = "import sys, widget; print(widget.activate_existing(sys.argv[1], sys.argv[2]))"
        process = subprocess.Popen([sys.executable, "-E", "-c", code, str(socket_path), mode], cwd=Path(__file__).parent,
                                   env=dict(os.environ, QT_QPA_PLATFORM="offscreen"), stdout=subprocess.PIPE, text=True)
        while process.poll() is None:
            QTest.qWait(10)
        return process.communicate()[0].strip()

    def test_second_launch_shows_existing_hidden_window_and_recovers_position(self):
        socket_path = Path(self.directory.name) / "widget.sock"
        server = listen_for_activation(self.window, socket_path)
        try:
            self.window.move(-10000, -10000)
            self.window.hide()
            self.assertEqual(self.activate_from_process(socket_path), "ok")
            QTest.qWait(50)
            self.assertTrue(self.window.isVisible())
            self.assertTrue(self.app.primaryScreen().availableGeometry().contains(self.window.geometry()))
            self.assertEqual(activate_existing(Path(self.directory.name) / "missing.sock"), "")
            with patch.object(self.window, "close") as close:
                self.assertEqual(self.activate_from_process(socket_path, "desktop"), "switch")
                QTest.qWait(50)
                close.assert_called_once()
            server.close()
            self.assertEqual(self.activate_from_process(socket_path), "")
        finally:
            server.close()

    def test_plan_labels_preserve_raw_names_and_mark_manual_values(self):
        self.assertEqual(self.window.cards["codex"].plan_badge.text(), "prolite")
        self.assertEqual(self.window.plan_text("codex"), "플랜 · prolite")
        self.assertEqual(self.window.cards["copilot"].plan_badge.text(), "Copilot Pro")
        self.assertEqual(self.window.cards["antigravity"].plan.text(), "플랜 정보 미제공")
        with patch("widget.QInputDialog.getText", return_value=("Google AI Pro", True)):
            self.window.edit_antigravity_plan()
        self.assertEqual(self.window.cards["antigravity"].plan_badge.text(), "Google AI Pro")
        self.assertIn("직접 설정", self.window.cards["antigravity"].plan.text())
        self.assertEqual(self.window.plan_text("antigravity"), "플랜 · Google AI Pro (직접 설정)")
        self.window.received("copilot", None, "연결 실패")
        self.assertEqual(self.window.cards["copilot"].plan_badge.text(), "Copilot Pro")
        self.window.received("copilot", {"rows": ROWS["copilot"], "plan": None}, "")
        self.assertEqual(self.window.cards["copilot"].plan.text(), "플랜 확인 안 됨")
        self.assertTrue(self.window.cards["copilot"].plan_badge.isHidden())


if __name__ == "__main__":
    unittest.main()
