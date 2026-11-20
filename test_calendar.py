"""Offline regressions: dates, pagination, OAuth storage, retry and UI behavior."""
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication, QDateTime, QEvent, QTime, QTimer
from PySide6.QtWidgets import QApplication, QDialog
from calendar_client import (CalendarClient, CalendarError, day_bounds, event_body,
                             event_text, TOKEN_URI, AUTH_URI, EVENTS_URL)
from calendar_panel import CalendarPanel, EventDialog
from quick_event import parse_quick_event


class CalendarTests(unittest.TestCase):
    def test_local_day_bounds_include_dst_transition(self):
        try:
            with patch.dict(os.environ, TZ="America/New_York"):
                time.tzset()
                lower, upper = map(datetime.fromisoformat, day_bounds(date(2026, 3, 8)))
                self.assertEqual((upper - lower).total_seconds(), 23 * 3600)
                self.assertEqual(lower.hour, 0)
                self.assertEqual(upper.hour, 0)
        finally:
            time.tzset()

    def test_event_validation_and_all_day_exclusive_end(self):
        start = datetime(2026, 9, 11, 23, 30, tzinfo=timezone.utc)
        body = event_body(" Meeting ", start, start + timedelta(hours=1))
        self.assertEqual(body["summary"], "Meeting")
        self.assertIn("2026-09-12", body["end"]["dateTime"])
        for title, end in [("", start + timedelta(hours=1)), ("x", start)]:
            with self.assertRaises(CalendarError):
                event_body(title, start, end)
        body = event_body("휴가", date(2026, 9, 11), date(2026, 9, 13), True)
        self.assertEqual(body["end"], {"date": "2026-09-13"})
        self.assertEqual(event_text(body), "종일 · 휴가")

    def test_today_expands_recurring_events_and_reads_every_page(self):
        client = CalendarClient("/unused")
        with patch.object(client, "request", side_effect=[
            {"items": [{"id": "one"}], "nextPageToken": "next"},
            {"items": [{"id": "two"}, {"id": "cancelled", "status": "cancelled"}]}]) as request:
            result = client.today(date(2026, 9, 11))
        self.assertEqual([e["id"] for e in result["events"]], ["one", "two"])
        params = request.call_args.kwargs["params"]
        self.assertEqual(params["singleEvents"], "true")
        self.assertEqual(params["pageToken"], "next")

    def test_retry_conflict_recovers_existing_event_and_protects_token(self):
        with tempfile.TemporaryDirectory() as directory:
            client = CalendarClient(directory)
            credentials = MagicMock()
            credentials.to_json.return_value = json.dumps({"token_uri": TOKEN_URI})
            client.save_token(credentials)
            self.assertEqual(client.token_path.stat().st_mode & 0o777, 0o600)
            conflict = MagicMock(status_code=409)
            success = MagicMock(status_code=200, ok=True)
            success.json.return_value = {"id": "abc123"}
            with patch("google.oauth2.credentials.Credentials.from_authorized_user_info", return_value=credentials), \
                    patch("google.auth.transport.requests.AuthorizedSession") as session:
                session.return_value.__enter__.return_value.request.side_effect = [conflict, success]
                self.assertEqual(client.add({"id": "abc123"}), {"id": "abc123"})
                request = session.return_value.__enter__.return_value.request
                self.assertEqual(request.call_args.args, ("GET", EVENTS_URL + "/abc123"))
                self.assertEqual(request.call_args.kwargs["timeout"], 30)
            client.disconnect()
            self.assertFalse(client.connected())

    def test_oauth_rejects_wrong_client_and_uses_loopback_pkce(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "client.json"
            client = CalendarClient(Path(directory) / "saved")
            source.write_text(json.dumps({"web": {}}))
            with self.assertRaises(CalendarError):
                client.connect(source)
            source.write_text(json.dumps({"installed": {"auth_uri": AUTH_URI,
                "token_uri": TOKEN_URI, "client_id": "test", "client_secret": "test"}}))
            with patch("google_auth_oauthlib.flow.InstalledAppFlow.from_client_config") as factory:
                flow = factory.return_value
                flow.run_local_server.return_value.to_json.return_value = "{}"
                client.connect(source)
                self.assertTrue(factory.call_args.kwargs["autogenerate_code_verifier"])
                self.assertEqual(flow.run_local_server.call_args.kwargs["host"], "127.0.0.1")
                self.assertEqual(flow.run_local_server.call_args.kwargs["timeout_seconds"], 180)
                self.assertEqual(flow.fetch_token.keywords["timeout"], 30)

    def test_forbidden_reason_selects_rate_limit_or_organizer_message(self):
        with tempfile.TemporaryDirectory() as directory:
            client = CalendarClient(directory)
            credentials = MagicMock()
            credentials.to_json.return_value = json.dumps({"token_uri": TOKEN_URI})
            client.save_token(credentials)
            response = MagicMock(status_code=403)
            with patch("google.oauth2.credentials.Credentials.from_authorized_user_info", return_value=credentials), \
                    patch("google.auth.transport.requests.AuthorizedSession") as session:
                session.return_value.__enter__.return_value.request.return_value = response
                for reason, message in (("rateLimitExceeded", "한도 초과"), ("userRateLimitExceeded", "한도 초과"),
                                        ("quotaExceeded", "한도 초과"), ("forbiddenForNonOrganizer", "주최자만"),
                                        ("insufficientPermissions", "다시 연결")):
                    response.json.return_value = {"error": {"code": 403, "errors": [{"reason": reason}]}}
                    with self.subTest(reason=reason), self.assertRaisesRegex(CalendarError, message):
                        client.today(date(2026, 10, 2))
                response.json.side_effect = ValueError("not JSON")
                with self.assertRaisesRegex(CalendarError, "다시 연결"):
                    client.today(date(2026, 10, 2))

    def test_quick_event_hour_minute_duration_needs_title_and_rejects_overflow(self):
        now = datetime(2026, 10, 2, 9, 0).astimezone()
        body = parse_quick_event("오늘 15:00 2시간 30분 동안 회의", now)
        self.assertEqual(body["summary"], "회의")
        begin, end = (datetime.fromisoformat(body[key]["dateTime"]) for key in ("start", "end"))
        self.assertEqual(end - begin, timedelta(hours=2, minutes=30))
        for text, message in (("오늘 18:30 30분 동안", "제목"), ("오늘 15:00 2시간 30분 동안", "제목"),
                              ("9999-12-31 종일 x", "날짜 또는 시간")):
            with self.subTest(text=text), self.assertRaisesRegex(CalendarError, message):
                parse_quick_event(text, now)


class CalendarUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_closed_modal_dialogs_release_their_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            panel = CalendarPanel(client=CalendarClient(directory))
            event = {"id": "test", "etag": "etag", "summary": "회의",
                     "start": {"date": "2026-09-29"}, "end": {"date": "2026-09-30"}}
            try:
                for action in (panel.add_event, lambda: panel.edit_event(event),
                               panel.quick_event, panel.connect_google):
                    with self.subTest(action=action.__name__):
                        for accepted in (False, True, False):
                            panel.quick_input.setText("내일 오후 3시 회의")
                            def finish():
                                dialog = self.app.activeModalWidget()
                                dialog.accept() if accepted else dialog.reject()
                            QTimer.singleShot(0, finish)
                            with patch.object(panel.client, "connected", return_value=action != panel.connect_google):
                                action()
                            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                            self.assertEqual(panel.findChildren(QDialog), [])
                            if action == panel.quick_event:
                                self.assertEqual(panel.quick_input.text(), "" if accepted else "내일 오후 3시 회의")
            finally:
                panel.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_panel_errors_keep_snapshot_disconnect_clears_it(self):
        with tempfile.TemporaryDirectory() as directory:
            client = CalendarClient(directory)
            panel = CalendarPanel(client=client)
            self.assertFalse(panel.add_button.isEnabled())
            snapshot = {"day": datetime.now().astimezone().date(), "events": [
                {"summary": "<b>literal title</b>", "start": {"date": "2026-09-11"},
                 "end": {"date": "2026-09-12"}}]}
            panel.received(snapshot, "")
            before = panel.listing.text()
            panel.received(None, "네트워크 오류")
            self.assertEqual(panel.listing.text(), before)
            self.assertIn("마지막 조회", panel.status.text())
            panel.disconnect()
            self.assertNotIn("literal title", panel.listing.text())
            panel.deleteLater()

    def test_keywords_preserve_details_and_calendar_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            panel = CalendarPanel(client=CalendarClient(directory))
            dialog = EventDialog(panel)
            dialog.title_input.setText("대전 고객사 방문")
            dialog.keyword_buttons["출장"].click()
            self.assertEqual(dialog.title_input.text(), "[출장] 대전 고객사 방문")
            dialog.keyword_buttons["학사일정"].click()
            dialog.keyword_buttons["학사일정"].click()
            self.assertEqual(dialog.title_input.text(), "[학사일정] 대전 고객사 방문")
            self.assertFalse(dialog.keyword_buttons["출장"].isChecked())
            dialog.title_input.setText("x" * 300)
            dialog.keyword_buttons["연차"].click()
            self.assertEqual(dialog.title_input.text(), "x" * 300)
            self.assertFalse(dialog.keyword_buttons["연차"].isChecked())
            for field in (dialog.start, dialog.end):
                calendar = field.calendarWidget()
                chosen = field.date().addDays(5)
                calendar.clicked.emit(chosen)
                self.assertEqual(field.date(), chosen)
                self.assertGreaterEqual(calendar.minimumWidth(), 360)
            dialog.close()
            panel.deleteLater()

    def test_custom_keywords_persist_validate_and_delete_without_changing_title(self):
        with tempfile.TemporaryDirectory() as directory:
            panel = CalendarPanel(client=CalendarClient(directory))
            dialog = EventDialog(panel)
            for word in ("공과금", "재택근무", "외근", "개인 약속", "병원 예약"):
                self.assertNotIn(word, dialog.keyword_buttons)
            with patch("calendar_panel.QInputDialog.getText", return_value=("  운동  ", True)):
                dialog.add_keyword()
                dialog.add_keyword()
            self.assertEqual(dialog.title_keywords.count("운동"), 1)
            dialog.title_input.setText("헬스장 방문")
            dialog.keyword_buttons["운동"].click()
            self.assertEqual(dialog.title_input.text(), "[운동] 헬스장 방문")
            restored = EventDialog(panel)
            self.assertIn("운동", restored.keyword_buttons)
            before = dialog.title_keywords.copy()
            for word in ("", "[운동]", "x" * 21, "두\n줄"):
                with patch("calendar_panel.QInputDialog.getText", return_value=(word, True)):
                    dialog.add_keyword()
            self.assertEqual(dialog.title_keywords, before)
            with patch("calendar_panel.QInputDialog.getItem", return_value=("운동", True)):
                dialog.remove_keyword()
            self.assertEqual(dialog.title_input.text(), "[운동] 헬스장 방문")
            dialog.keyword_buttons["출장"].click()
            self.assertEqual(dialog.title_input.text(), "[출장] 헬스장 방문")
            dialog.store_keywords([])
            empty = EventDialog(panel)
            self.assertEqual(empty.title_keywords, [])
            for item in (dialog, restored, empty):
                item.close()
            panel.deleteLater()

    def test_add_retries_same_id_and_all_day_end_is_inclusive_in_form(self):
        with tempfile.TemporaryDirectory() as directory:
            panel = CalendarPanel(client=CalendarClient(directory))
            dialog = EventDialog(panel)
            dialog.title_input.setText("할 일")
            dialog.all_day.setChecked(True)
            dialog.end.setDate(dialog.start.date())
            bodies = []

            def run(action, callback):
                action()
                callback(None, "응답 시간 초과")

            with patch.object(panel, "run", side_effect=run), \
                    patch.object(panel.client, "add", side_effect=lambda body: bodies.append(body.copy())):
                dialog.save()
                dialog.save()
            self.assertEqual(bodies[0]["id"], bodies[1]["id"])
            self.assertEqual(date.fromisoformat(bodies[0]["end"]["date"]),
                             dialog.start.date().toPython() + timedelta(days=1))
            self.assertTrue(dialog.submit.isEnabled())
            dialog.close()
            panel.deleteLater()

    def test_single_and_empty_keyword_lists_survive_restart(self):
        for words in (["운동"], []):
            with self.subTest(words=words), tempfile.TemporaryDirectory() as first, \
                    tempfile.TemporaryDirectory() as second:
                panel = CalendarPanel(client=CalendarClient(first))
                EventDialog(panel).store_keywords(words)
                # Qt caches INI files per path in-process; a copy reads like the next launch.
                shutil.copy(Path(first) / "keywords.ini", second)
                restarted = CalendarPanel(client=CalendarClient(second))
                self.assertEqual(EventDialog(restarted).title_keywords, words)
                for item in (panel, restarted):
                    item.deleteLater()

    def test_day_change_during_running_job_refreshes_after_it_ends(self):
        with tempfile.TemporaryDirectory() as directory:
            panel = CalendarPanel(client=CalendarClient(directory))
            panel.checked_day -= timedelta(days=1)
            with patch.object(panel, "refresh") as refresh:
                panel.job = object()  # e.g. a refresh that started before midnight
                panel.tick()
                panel.job = None
                refresh.reset_mock()
                panel.tick()
                panel.tick()
                refresh.assert_called_once()
            panel.deleteLater()

    def test_saved_times_drop_seconds_hidden_by_the_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            panel = CalendarPanel(client=CalendarClient(directory))
            dialog = EventDialog(panel)
            self.assertEqual((dialog.start.time().second(), dialog.start.time().msec()), (0, 0))
            dialog.title_input.setText("회의")
            start = QDateTime(dialog.start.date(), QTime(15, 0, 14))
            dialog.start.setDateTime(start)
            dialog.end.setDateTime(start.addSecs(3600))
            bodies = []
            with patch.object(panel, "run", side_effect=lambda action, callback: (action(), callback(None, "x"))), \
                    patch.object(panel.client, "add", side_effect=bodies.append):
                dialog.save()
            begin, end = (datetime.fromisoformat(bodies[0][key]["dateTime"]) for key in ("start", "end"))
            self.assertEqual((begin.strftime("%H:%M:%S"), end.strftime("%H:%M:%S")), ("15:00:00", "16:00:00"))
            dialog.close()
            panel.deleteLater()

    def test_save_and_delete_during_background_job_explain_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            panel = CalendarPanel(client=CalendarClient(directory))
            dialog = EventDialog(panel, {"id": "e", "etag": "t", "summary": "회의",
                                         "start": {"date": "2026-10-05"}, "end": {"date": "2026-10-06"}})
            panel.job = object()
            with patch.object(panel, "run") as run:
                for action in (dialog.save, dialog.delete_event):
                    dialog.status.clear()
                    action()
                    self.assertIn("다시 눌러", dialog.status.text())
                run.assert_not_called()
            panel.job = None
            dialog.close()
            panel.deleteLater()

    def test_edit_keeps_long_title_and_existing_empty_title(self):
        with tempfile.TemporaryDirectory() as directory:
            panel = CalendarPanel(client=CalendarClient(directory))
            times = {"id": "e", "etag": "t", "start": {"dateTime": "2026-10-05T10:00:00+09:00"},
                     "end": {"dateTime": "2026-10-05T11:00:00+09:00"}}
            bodies = []
            with patch.object(panel, "run", side_effect=lambda action, callback: (action(), callback(None, "x"))), \
                    patch.object(panel.client, "update", side_effect=lambda event, body: bodies.append(body)):
                for event in (dict(times, summary="가" * 350), times):
                    dialog = EventDialog(panel, event)
                    dialog.end.setDateTime(dialog.end.dateTime().addSecs(1800))
                    dialog.save()
                    dialog.close()
                titled = EventDialog(panel, dict(times, summary="회의"))
                titled.title_input.clear()
                titled.save()
                self.assertIn("제목", titled.status.text())
                titled.close()
            self.assertEqual([body["summary"] for body in bodies], ["가" * 350, ""])
            self.assertEqual(EventDialog(panel).title_input.maxLength(), 300)
            panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
