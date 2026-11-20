"""Google Calendar primary-calendar access; credentials never enter UI/logs."""
import json
import os
from pathlib import Path
import sys
import tempfile
from functools import partial
from urllib.parse import quote
from datetime import datetime, time, timedelta

sys.path.append(str(Path(__file__).parent / "vendor"))

SCOPE = "https://www.googleapis.com/auth/calendar.events.owned"
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


class CalendarError(Exception):
    pass


def day_bounds(day):
    # Convert each midnight separately so local DST transitions remain correct.
    return tuple(datetime.combine(d, time.min).astimezone().isoformat()
                 for d in (day, day + timedelta(days=1)))


def event_body(title, start, end, all_day=False, limit=300, required=True):
    # Edits pass a larger limit / required=False to keep an existing long or empty title.
    title = title.strip()
    if (required and not title) or len(title) > limit:
        raise CalendarError("제목을 1–300자로 입력하세요.")
    if end <= start:
        raise CalendarError("종료는 시작보다 늦어야 합니다.")
    key = "date" if all_day else "dateTime"
    if not all_day and (start.tzinfo is None or end.tzinfo is None):
        raise CalendarError("일정 시간대가 필요합니다.")
    return {"summary": title, "start": {key: start.isoformat()},
            "end": {key: end.isoformat()}}


def event_text(event, now=None):
    now = now or datetime.now().astimezone()
    title = event.get("summary") or "(제목 없음)"
    start, end = event["start"], event["end"]
    if "date" in start:
        return "종일 · " + title
    begin = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00")).astimezone()
    finish = datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00")).astimezone()
    status = "진행 중 · " if begin <= now < finish else "종료 · " if finish <= now else ""
    fmt = "%H:%M" if begin.date() == finish.date() else "%m/%d %H:%M"
    return f"{begin.strftime(fmt)}–{finish.strftime(fmt)} · {status}{title}"


class CalendarClient:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else Path(
            os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "wego/check-ai-calendar"
        self.token_path = self.directory / "token.json"

    def connected(self):
        return self.token_path.is_file()

    def save_token(self, credentials):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(dir=self.directory, prefix=".token-")
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(credentials.to_json())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(path, self.token_path)
        finally:
            Path(path).unlink(missing_ok=True)

    def connect(self, source=None):
        from google_auth_oauthlib.flow import InstalledAppFlow
        if source:
            try:
                config = json.loads(Path(source).read_text())
                installed = config["installed"]
                if (installed["auth_uri"] != AUTH_URI or installed["token_uri"] != TOKEN_URI
                        or not installed["client_id"] or not installed["client_secret"]):
                    raise ValueError()
            except (OSError, ValueError, KeyError, TypeError):
                raise CalendarError("Google Cloud에서 받은 ‘데스크톱 앱’ OAuth JSON을 선택하세요.") from None
        else:
            saved = json.loads(self.token_path.read_text())
            config = {"installed": {"client_id": saved["client_id"],
                                    "client_secret": saved["client_secret"],
                                    "auth_uri": AUTH_URI, "token_uri": TOKEN_URI}}
        flow = InstalledAppFlow.from_client_config(config, [SCOPE], autogenerate_code_verifier=True)
        flow.fetch_token = partial(flow.fetch_token, timeout=30)
        credentials = flow.run_local_server(
            host="127.0.0.1", port=0, timeout_seconds=180,
            authorization_prompt_message="", success_message="Connected. You may close this tab.",
            prompt="consent")
        if not credentials.has_scopes([SCOPE]):
            raise CalendarError("캘린더 일정 접근 권한을 허용한 뒤 다시 연결하세요.")
        self.save_token(credentials)

    def disconnect(self):
        self.token_path.unlink(missing_ok=True)

    def request(self, method, url=EVENTS_URL, **kwargs):
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import AuthorizedSession
        from google.auth.exceptions import RefreshError
        if not self.connected():
            raise CalendarError("Google 연결을 먼저 진행하세요.")
        try:
            saved = json.loads(self.token_path.read_text())
            if saved.get("token_uri") != TOKEN_URI:
                raise CalendarError("저장된 연결 정보가 올바르지 않습니다. 다시 연결하세요.")
            credentials = Credentials.from_authorized_user_info(saved, [SCOPE])
            with AuthorizedSession(credentials, refresh_timeout=30) as session:
                response = session.request(method, url, timeout=30, **kwargs)
            self.save_token(credentials)
        except RefreshError:
            raise CalendarError("Google 로그인이 만료되었거나 해제되었습니다. 다시 연결하세요.") from None
        if response.status_code == 409 and method == "POST":
            # A retry uses the same event ID even if the previous response was lost.
            return self.request("GET", EVENTS_URL + "/" + kwargs["json"]["id"])
        if response.status_code == 403:
            try:
                reasons = {item.get("reason") for item in response.json()["error"]["errors"]}
            except (ValueError, KeyError, TypeError, AttributeError):
                reasons = set()
            if reasons & {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"}:
                raise CalendarError("Google Calendar 요청 한도 초과. 잠시 후 다시 시도하세요.")
            if "forbiddenForNonOrganizer" in reasons:
                raise CalendarError("주최자만 수정할 수 있는 일정입니다.")
        if response.status_code in (401, 403):
            raise CalendarError("Calendar API 활성화·테스트 사용자·일정 권한 확인 후 다시 연결하세요.")
        if response.status_code == 412:
            raise CalendarError("다른 곳에서 일정이 변경되었습니다. 목록을 갱신한 뒤 다시 열어 주세요.")
        if method == "DELETE" and response.status_code == 410:
            return {}
        if not response.ok:
            raise CalendarError(f"Google Calendar 요청 실패 (HTTP {response.status_code}). 잠시 후 재시도하세요.")
        return {} if response.status_code == 204 else response.json()

    def today(self, day=None, days=1):
        day = day or datetime.now().astimezone().date()
        lower, upper = day_bounds(day)
        if days not in (1, 7):
            raise CalendarError("조회 기간은 1일 또는 7일이어야 합니다.")
        upper = day_bounds(day + timedelta(days=days - 1))[1]
        params = {"timeMin": lower, "timeMax": upper, "singleEvents": "true",
                  "orderBy": "startTime", "maxResults": 2500}
        events = []
        while True:
            page = self.request("GET", params=params)
            events.extend(e for e in page.get("items", []) if e.get("status") != "cancelled")
            if not page.get("nextPageToken"):
                return {"day": day, "events": events}
            params["pageToken"] = page["nextPageToken"]

    def add(self, body):
        return self.request("POST", json=body, params={"sendUpdates": "none"})

    def update(self, event, body):
        body = dict(body)
        # PATCH merges nested fields; explicitly remove the old date representation.
        for key in ("start", "end"):
            body[key] = dict(body[key])
            body[key]["dateTime" if "date" in body[key] else "date"] = None
            if "date" in body[key] and body[key]["date"] is not None:
                body[key]["timeZone"] = None
        return self.request("PATCH", EVENTS_URL + "/" + quote(event["id"], safe=""),
                            json=body, headers={"If-Match": event["etag"]},
                            params={"sendUpdates": "none"})

    def delete(self, event):
        return self.request("DELETE", EVENTS_URL + "/" + quote(event["id"], safe=""),
                            headers={"If-Match": event["etag"]}, params={"sendUpdates": "none"})
