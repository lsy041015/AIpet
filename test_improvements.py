"""Offline checks for calendar mutations, views and persistent notification policy."""
import tempfile
import json
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch, Mock

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from calendar_client import CalendarClient, event_body, EVENTS_URL
from calendar_client import CalendarError, TOKEN_URI
from calendar_panel import CalendarPanel, EventDialog
from notifications import Notifications
from quick_event import parse_quick_event


class ImprovementsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = CalendarClient(self.tmp.name)
        self.settings = QSettings(str(Path(self.tmp.name) / "settings.ini"), QSettings.IniFormat)
        self.now = datetime.now().astimezone()

    def tearDown(self):
        self.tmp.cleanup()

    def event(self, minutes=6, event_id="a"):
        start = self.now + timedelta(minutes=minutes)
        return dict(event_body("<b>일정</b>", start, start + timedelta(hours=1)), id=event_id, etag='"version1"')

    def test_quick_input_dates_period_duration_and_validation(self):
        now = datetime(2026, 12, 31, 10).astimezone()
        body = parse_quick_event("내일 오후 3시 30분 동안 회의", now)
        start = datetime.fromisoformat(body["start"]["dateTime"])
        end = datetime.fromisoformat(body["end"]["dateTime"])
        self.assertEqual((start.year, start.month, start.day, start.hour), (2027, 1, 1, 15))
        self.assertEqual((end - start).total_seconds(), 1800)
        self.assertEqual(body["summary"], "회의")
        self.assertEqual(parse_quick_event("모레 종일 출장", now)["end"]["date"], "2027-01-03")
        for phrase, hour in (("오늘 오전 12시 회의", 0), ("오늘 오후 12시 회의", 12), ("2027-01-04 18:30 운동", 18)):
            self.assertEqual(datetime.fromisoformat(parse_quick_event(phrase, now)["start"]["dateTime"]).hour, hour)
        for phrase in ("회의", "내일 25:00 회의", "2026-02-30 12:00 회의", "내일 오후 13시 회의", "내일 15:00 0분 동안 회의", "내일 3시 반 회의"):
            with self.assertRaises(CalendarError, msg=phrase):
                parse_quick_event(phrase, now)

    def test_quick_input_only_previews_before_save(self):
        panel = CalendarPanel(client=self.client)
        panel.quick_input.setText("내일 오후 3시 회의")
        with patch.object(self.client, "connected", return_value=True), patch("calendar_panel.EventDialog") as factory, patch.object(self.client, "add") as add:
            factory.return_value.exec.return_value = 0
            panel.quick_event()
            factory.return_value.title_input.setText.assert_called_with("회의")
            add.assert_not_called()
            self.assertEqual(panel.quick_input.text(), "내일 오후 3시 회의")
        panel.deleteLater()

    def test_mutations_use_instance_id_etag_and_preserve_other_fields(self):
        event = self.event()
        event["id"] = "instance/one"
        body = event_body("종일", self.now.date(), self.now.date() + timedelta(days=1), True)
        with patch.object(self.client, "request", return_value={}) as request:
            self.client.update(event, body)
            self.assertEqual(request.call_args.args, ("PATCH", EVENTS_URL + "/instance%2Fone"))
            self.assertEqual(request.call_args.kwargs["headers"], {"If-Match": '"version1"'})
            payload = request.call_args.kwargs["json"]
            self.assertIsNone(payload["start"]["dateTime"])
            self.assertNotIn("attendees", payload)
            self.client.delete(event)
            self.assertEqual(request.call_args.args[0], "DELETE")
            self.assertEqual(request.call_args.kwargs["params"], {"sendUpdates": "none"})
            self.client.today(self.now.date(), days=7)
            params = request.call_args.kwargs["params"]
            self.assertEqual(datetime.fromisoformat(params["timeMax"]).date(), self.now.date() + timedelta(days=7))

    def test_empty_delete_response_and_concurrent_edit_are_handled(self):
        credentials = Mock()
        credentials.to_json.return_value = json.dumps({"token_uri": TOKEN_URI})
        self.client.save_token(credentials)
        with patch("google.oauth2.credentials.Credentials.from_authorized_user_info", return_value=credentials), patch("google.auth.transport.requests.AuthorizedSession") as session:
            request = session.return_value.__enter__.return_value.request
            response = Mock(status_code=204, ok=True)
            request.return_value = response
            self.assertEqual(self.client.delete(self.event()), {})
            response.json.assert_not_called()
            request.return_value = Mock(status_code=412, ok=False)
            with self.assertRaisesRegex(CalendarError, "변경"):
                self.client.update(self.event(), self.event())

    def test_edit_prefills_all_day_dates_and_delete_requires_confirmation(self):
        panel = CalendarPanel(client=self.client)
        event = dict(event_body("휴가", date(2026, 9, 18), date(2026, 9, 21), True), id="a", etag='"1"', recurringEventId="parent")
        dialog = EventDialog(panel, event)
        self.assertTrue(dialog.all_day.isChecked())
        self.assertEqual(dialog.end.date().toPython(), date(2026, 9, 20))
        with patch.object(panel, "run") as run, patch("calendar_panel.QMessageBox.question", return_value=QMessageBox.No):
            dialog.delete_event()
            run.assert_not_called()
        with patch.object(panel, "run") as run, patch.object(self.client, "delete") as delete, patch("calendar_panel.QMessageBox.question", return_value=QMessageBox.Yes):
            dialog.delete_event()
            run.call_args.args[0]()
            delete.assert_called_once_with(event)
            dialog.saving = False
        dialog.close()
        panel.deleteLater()

    def test_edit_saves_existing_event_without_insert(self):
        panel = CalendarPanel(client=self.client)
        event = self.event()
        dialog = EventDialog(panel, event)
        dialog.title_input.setText("변경된 제목")
        with patch.object(panel, "run") as run, patch.object(self.client, "update") as update, patch.object(self.client, "add") as add:
            dialog.save()
            run.call_args.args[0]()
            self.assertEqual(update.call_args.args[0]["id"], event["id"])
            self.assertEqual(update.call_args.args[1]["summary"], "변경된 제목")
            add.assert_not_called()
            dialog.saving = False
        dialog.close()
        panel.deleteLater()

    def test_today_tomorrow_week_cards_and_stale_day(self):
        panel = CalendarPanel(client=self.client)
        today = self.now.date()
        events = [dict(event_body(name, today + timedelta(days=offset), today + timedelta(days=offset + 1), True), id=name, etag="1")
                  for offset, name in ((0, "오늘"), (1, "내일"), (6, "마지막"), (7, "제외"))]
        panel.received({"day": today, "events": events}, "")
        self.assertEqual(panel.event_layout.count(), 1)
        panel.range_select.setCurrentIndex(1)
        self.assertIn("내일", panel.listing.text())
        self.assertEqual(panel.event_layout.count(), 1)
        panel.range_select.setCurrentIndex(2)
        self.assertEqual(panel.event_layout.count(), 3)
        panel.loaded_day = today - timedelta(days=1)
        panel.render()
        self.assertEqual(panel.event_layout.count(), 0)
        panel.deleteLater()

    def test_calendar_notifications_deduplicate_restart_and_reschedule(self):
        send = Mock(return_value=True)
        alerts = Notifications(self.settings, send)
        event = self.event()
        alerts.calendar([event], self.now)
        alerts.calendar([event], self.now)
        self.assertEqual(send.call_count, 1)
        Notifications(self.settings, send).calendar([event], self.now)
        self.assertEqual(send.call_count, 1)
        event["start"]["dateTime"] = (self.now + timedelta(minutes=8)).isoformat()
        alerts.calendar([event], self.now)
        self.assertEqual(send.call_count, 2)
        self.settings.setValue("notify/calendar", 5)
        alerts.calendar([self.event(event_id="b")], self.now)
        self.assertEqual(send.call_count, 2)
        self.settings.setValue("notify/calendar", 0)
        alerts.calendar([self.event(2, "c")], self.now)
        self.assertEqual(send.call_count, 2)

    def test_quota_thresholds_recovery_reset_disable_and_failed_delivery(self):
        send = Mock(return_value=True)
        alerts = Notifications(self.settings, send)
        row = {"label": "주간", "remaining": 18, "reset": "next"}
        alerts.quota("codex", "Codex", [row])
        alerts.quota("codex", "Codex", [row])
        self.assertEqual(send.call_count, 1)
        row["remaining"] = 9
        alerts.quota("codex", "Codex", [row])
        self.assertEqual(send.call_count, 2)
        row["reset"] = "new"
        alerts.quota("codex", "Codex", [row])
        self.assertEqual(send.call_count, 3)
        self.settings.setValue("notify/codex/20", False)
        self.settings.setValue("notify/codex/10", False)
        row["reset"] = "later"
        alerts.quota("codex", "Codex", [row])
        self.assertEqual(send.call_count, 3)
        send.return_value = False
        alerts.calendar([self.event()], self.now)
        send.return_value = True
        alerts.calendar([self.event()], self.now)
        self.assertEqual(send.call_count, 5)


if __name__ == "__main__":
    unittest.main()
