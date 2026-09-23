"""Offline regressions: dates, pagination, OAuth storage, retry and UI behavior."""
import json
import os
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication
from calendar_client import (CalendarClient, CalendarError, day_bounds, event_body,
                             event_text, TOKEN_URI, AUTH_URI, EVENTS_URL)
from calendar_panel import CalendarPanel, EventDialog


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


class CalendarUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

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


if __name__ == "__main__":
    unittest.main()
