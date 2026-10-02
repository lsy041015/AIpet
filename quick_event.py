"""Small explicit Korean date grammar. Always preview in EventDialog before writing."""
import re
from datetime import datetime, timedelta
from calendar_client import CalendarError, event_body


def parse_quick_event(text, now=None):
    now = now or datetime.now().astimezone()
    match = re.fullmatch(r"\s*(오늘|내일|모레|\d{4}-\d{1,2}-\d{1,2})\s+(.+?)\s*", text)
    if not match:
        raise CalendarError("날짜부터 입력하세요. 예: 내일 오후 3시 회의")
    day, rest = match.groups()
    try:
        day = (now.date() + timedelta(days={"오늘": 0, "내일": 1, "모레": 2}[day])
               if day in ("오늘", "내일", "모레") else datetime.strptime(day, "%Y-%m-%d").date())
        if rest.startswith("종일 "):
            return event_body(rest[3:], day, day + timedelta(days=1), True)
        match = re.fullmatch(r"(?:(오전|오후)\s*)?(\d{1,2})(?::(\d{2})|시(?:\s*(\d{1,2})분(?!\s*동안))?)\s+(.+)", rest)
        if not match:
            raise CalendarError("시간과 제목을 확인하세요. 예: 내일 오후 3시 회의 / 오늘 18:30 운동")
        period, hour, minute, korean_minute, title = match.groups()
        hour, minute = int(hour), int(minute or korean_minute or 0)
        if not 0 <= minute <= 59 or not (1 <= hour <= 12 if period else 0 <= hour <= 23):
            raise ValueError()
        if period:
            hour = hour % 12 + (12 if period == "오후" else 0)
        duration = 60
        length = re.fullmatch(r"(?:(\d+)\s*시간)?\s*(?:(\d+)\s*분)?\s*동안(?:\s+(.+))?", title)
        if length and (length[1] or length[2]):
            hours, minutes, title = length.groups()
            title = title or ""  # event_body rejects a duration without a title.
            duration = int(hours or 0) * 60 + int(minutes or 0)
            if not 1 <= duration <= 1440:
                raise CalendarError("일정 길이는 1분–24시간으로 입력하세요.")
        if re.match(r"(?:반(?:\s|$)|부터|까지|[~–]|\d{1,2}(?:시|:))", title):
            raise CalendarError("시간 범위 대신 ‘30분 동안’처럼 입력하거나 상세 화면에서 수정하세요.")
        start = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute).astimezone()
        return event_body(title, start, start + timedelta(minutes=duration))
    except (ValueError, OverflowError):  # e.g. 9999-12-31 종일 has no next day.
        raise CalendarError("날짜 또는 시간 값이 올바르지 않습니다.") from None
