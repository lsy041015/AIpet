"""Persistent notification deduplication; delivery is supplied by the desktop widget."""
import json
from datetime import datetime
from hashlib import sha256


class Notifications:
    def __init__(self, settings, send):
        self.settings, self.send = settings, send
        try:
            self.state = json.loads(settings.value("notification_state", "{}"))
            if not isinstance(self.state, dict):
                self.state = {}
        except (ValueError, TypeError):
            self.state = {}

    def save(self):
        self.settings.setValue("notification_state", json.dumps(self.state))

    def quota(self, service, title, rows):
        for row in rows:
            remaining = row.get("remaining")
            if not isinstance(remaining, (int, float)):
                continue
            key = "quota:" + service + ":" + str(row.get("label", ""))
            reset = row.get("reset", "")
            previous = self.state.get(key, {})
            if not isinstance(previous, dict):
                previous = {}
            sent = previous.get("sent", []) if previous.get("reset") == reset else []
            sent = [t for t in sent if remaining <= t]
            due = [t for t in (20, 10) if remaining <= t and t not in sent
                   and self.settings.value(f"notify/{service}/{t}", True, type=bool)]
            if due and self.send(title + " 한도 알림", f"{row.get('label', '')} · {remaining:g}% 남음"):
                sent = sorted(set(sent + due))
            self.state[key] = {"reset": reset, "sent": sent}
        self.save()

    def calendar(self, events, now=None):
        now = now or datetime.now().astimezone()
        minutes = self.settings.value("notify/calendar", 10, type=int)
        if minutes not in (5, 10):
            return
        # Retain recent delivery keys across restarts, without storing event titles.
        for key, stamp in list(self.state.items()):
            if key.startswith("event:") and (not isinstance(stamp, (int, float)) or stamp < now.timestamp() - 86400 * 14):
                del self.state[key]
        for event in events:
            raw = event.get("start", {}).get("dateTime")
            if not raw or not event.get("id") or event.get("status") == "cancelled":
                continue
            start = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            seconds = (start - now).total_seconds()
            key = "event:" + sha256((event["id"] + raw).encode()).hexdigest()
            if 0 < seconds <= minutes * 60 and key not in self.state:
                title = str(event.get("summary") or "제목 없음")
                if self.send("곧 시작하는 일정", f"{start.astimezone():%H:%M} · {title}"):
                    self.state[key] = now.timestamp()
        self.save()
