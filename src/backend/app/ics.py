"""사용자가 확인한 일정으로 .ics(iCalendar) 파일을 만든다."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta

from pydantic import BaseModel, Field

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")


class IcsEvent(BaseModel):
    title: str = Field(max_length=200)
    date: str
    start: str | None = None
    end: str | None = None
    location: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=500)


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def _parse_time(value: str | None) -> tuple[int, int] | None:
    m = _TIME.match(value or "")
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h == 24 and mi == 0:
        return 23, 59
    if 0 <= h < 24 and 0 <= mi < 60:
        return h, mi
    return None


def build_ics(events: list[IcsEvent]) -> str:
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Biseosil//KO",
        "CALSCALE:GREGORIAN",
        "BEGIN:VTIMEZONE",
        "TZID:Asia/Seoul",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0900",
        "TZOFFSETTO:+0900",
        "TZNAME:KST",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]
    for ev in events:
        if not _DATE.match(ev.date):
            raise ValueError(f"날짜 형식이 올바르지 않습니다: {ev.date}")
        day = date.fromisoformat(ev.date)
        lines += ["BEGIN:VEVENT", f"UID:{uuid.uuid4()}@biseosil", f"DTSTAMP:{stamp}"]
        start = _parse_time(ev.start)
        if start:
            begin = datetime(day.year, day.month, day.day, *start)
            end_t = _parse_time(ev.end)
            finish = datetime(day.year, day.month, day.day, *end_t) if end_t else begin + timedelta(hours=1)
            if finish <= begin:
                finish = begin + timedelta(hours=1)
            lines.append(f"DTSTART;TZID=Asia/Seoul:{begin:%Y%m%dT%H%M%S}")
            lines.append(f"DTEND;TZID=Asia/Seoul:{finish:%Y%m%dT%H%M%S}")
        else:
            lines.append(f"DTSTART;VALUE=DATE:{day:%Y%m%d}")
            lines.append(f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}")
        lines.append(f"SUMMARY:{_escape(ev.title)}")
        if ev.location:
            lines.append(f"LOCATION:{_escape(ev.location)}")
        note = "비서실 AI가 만든 일정입니다. 원문을 확인하세요."
        if ev.note:
            note += "\n근거: " + ev.note
        lines.append(f"DESCRIPTION:{_escape(note)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
