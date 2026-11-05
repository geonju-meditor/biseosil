"""메일(붙여넣기·.eml)과 캘린더(.ics) 파서, 입력 채널 판별.

채널별 수집 비서가 쓰는 결정적 도구다. 결과는 카카오톡과 같은 `Room`/`Message` 구조로
맞춰 다른 비서가 채널과 상관없이 `s{입력}-m{번호}` ID로 근거를 인용할 수 있게 한다.
"""

from __future__ import annotations

import email
import re
from datetime import date, datetime, timedelta, timezone
from email import policy
from email.utils import parseaddr, parsedate_to_datetime

from .kakao import CalendarEntry, Message, Room, parse_export

KST = timezone(timedelta(hours=9))
CHANNEL_LABEL = {"kakao": "카카오톡", "mail": "메일", "calendar": "캘린더"}

_MAIL_START = re.compile(r"^(보낸\s*사람|From)\s*[:：]", re.IGNORECASE)
_HEADER = re.compile(r"^(?P<key>보낸\s*사람|받는\s*사람|참조|날짜|보낸\s*날짜|제목|From|To|Cc|Date|Sent|Subject)\s*[:：]\s*(?P<value>.*)$", re.IGNORECASE)
_SEPARATOR = re.compile(r"^\s*(-{3,}|={3,}|_{3,})\s*$")
_DATE_PATTERNS = [
    re.compile(r"(?P<y>\d{4})[-./]\s*(?P<m>\d{1,2})[-./]\s*(?P<d>\d{1,2})\.?\s*(?:\([^)]*\))?\s*(?:(?P<ampm>오전|오후)\s*)?(?:(?P<h>\d{1,2}):(?P<mi>\d{2}))?"),
    re.compile(r"(?P<y>\d{4})년\s*(?P<m>\d{1,2})월\s*(?P<d>\d{1,2})일\s*(?:\([^)]*\)|[월화수목금토일]요일)?\s*(?:(?P<ampm>오전|오후)\s*)?(?:(?P<h>\d{1,2}):(?P<mi>\d{2}))?"),
]


def detect_channel(text: str) -> str:
    head = text.lstrip("\ufeff").lstrip()[:2000]
    if "BEGIN:VCALENDAR" in head or "BEGIN:VEVENT" in head:
        return "calendar"
    if any(_MAIL_START.match(line.strip()) for line in head.splitlines()[:20]):
        return "mail"
    return "kakao"


def _parse_mail_date(value: str) -> tuple[str, str] | None:
    value = value.strip()
    for pattern in _DATE_PATTERNS:
        if m := pattern.search(value):
            h = int(m["h"]) if m["h"] else 9
            if m["ampm"] == "오후" and h < 12:
                h += 12
            if m["ampm"] == "오전" and h == 12:
                h = 0
            try:
                d = date(int(m["y"]), int(m["m"]), int(m["d"]))
            except ValueError:
                return None
            return d.isoformat(), f"{h:02d}:{m['mi'] or '00'}"
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if dt.tzinfo:
        dt = dt.astimezone(KST)
    return dt.date().isoformat(), dt.strftime("%H:%M")


def _sender_name(value: str) -> str:
    name, addr = parseaddr(value)
    return (name or addr or value).strip().strip('"') or "알 수 없음"


def _mail_message(index: int, n: int, sender: str, when: str | None, subject: str, body: str) -> Message | None:
    parsed = _parse_mail_date(when or "")
    if not parsed:
        return None
    body = "\n".join(line.rstrip() for line in body.strip().splitlines() if line.strip())
    text = f"[제목] {subject.strip() or '(제목 없음)'}\n{body}".strip()
    return Message(id=f"s{index + 1}-m{n}", sender=_sender_name(sender), date=parsed[0], time=parsed[1], text=text)


def _parse_eml(raw: str, index: int, name: str) -> Room:
    msg = email.message_from_string(raw, policy=policy.default)
    part = msg.get_body(preferencelist=("plain", "html"))
    body = part.get_content() if part is not None else ""
    if part is not None and part.get_content_type() == "text/html":
        body = re.sub(r"<[^>]+>", " ", body)
    room = Room(index=index, name=name, channel="mail")
    if m := _mail_message(index, 1, str(msg.get("From", "")), str(msg.get("Date", "")), str(msg.get("Subject", "")), body):
        room.messages.append(m)
    return room


def parse_mail(raw: str, index: int, name: str | None = None) -> Room:
    """붙여넣은 메일 여러 통(보낸 사람/날짜/제목 머리글) 또는 .eml 원문을 메시지로 나눈다."""

    text = raw.replace("\r\n", "\n").replace("\ufeff", "")
    name = name or f"메일 {index + 1}"
    if re.search(r"^(MIME-Version|Content-Type)\s*:", text, re.IGNORECASE | re.MULTILINE):
        return _parse_eml(text, index, name)
    room = Room(index=index, name=name, channel="mail")
    blocks: list[list[str]] = []
    for line in text.split("\n"):
        if _MAIL_START.match(line.strip()):
            blocks.append([])
        if blocks:
            blocks[-1].append(line)
    for block in blocks:
        headers: dict[str, str] = {}
        body_start = len(block)
        for i, line in enumerate(block):
            if not line.strip():
                body_start = i + 1
                break
            if h := _HEADER.match(line.strip()):
                key = re.sub(r"\s", "", h["key"].lower())
                headers[key] = h["value"]
        body = "\n".join(l for l in block[body_start:] if not _SEPARATOR.match(l))
        sender = headers.get("보낸사람") or headers.get("from", "")
        when = headers.get("날짜") or headers.get("보낸날짜") or headers.get("date") or headers.get("sent")
        subject = headers.get("제목") or headers.get("subject", "")
        if m := _mail_message(index, len(room.messages) + 1, sender, when, subject, body):
            room.messages.append(m)
    return room


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _ics_unescape(value: str) -> str:
    return value.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()


def _ics_time(prop: str, value: str) -> tuple[str, str | None] | None:
    value = value.strip()
    try:
        if "VALUE=DATE" in prop.upper() or len(value) == 8:
            return datetime.strptime(value[:8], "%Y%m%d").date().isoformat(), None
        dt = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    if value.endswith("Z"):
        dt = dt.replace(tzinfo=timezone.utc).astimezone(KST)
    return dt.date().isoformat(), dt.strftime("%H:%M")


def parse_calendar(raw: str, index: int, name: str | None = None) -> Room:
    room = Room(index=index, name=name or "내 캘린더", channel="calendar")
    current: dict[str, tuple[str, str]] | None = None
    for line in _unfold(raw.replace("\ufeff", "")):
        stripped = line.strip()
        if stripped == "BEGIN:VEVENT":
            current = {}
        elif stripped == "END:VEVENT" and current is not None:
            start = _ics_time(*current["DTSTART"]) if "DTSTART" in current else None
            if start:
                end = _ics_time(*current["DTEND"]) if "DTEND" in current else None
                room.events.append(
                    CalendarEntry(
                        id=f"s{index + 1}-c{len(room.events) + 1}",
                        title=_ics_unescape(current.get("SUMMARY", ("", "(제목 없음)"))[1]) or "(제목 없음)",
                        date=start[0],
                        start=start[1],
                        end=end[1] if end and end[0] == start[0] else None,
                        location=_ics_unescape(current["LOCATION"][1]) if "LOCATION" in current else None,
                    )
                )
            current = None
        elif current is not None and ":" in stripped:
            head, value = stripped.split(":", 1)
            key, _, params = head.partition(";")
            current[key.upper()] = (params, value)
    return room


def parse_input(text: str, index: int, name: str | None = None, channel: str | None = None) -> Room:
    channel = channel or detect_channel(text)
    if channel == "calendar":
        return parse_calendar(text, index, name)
    if channel == "mail":
        return parse_mail(text, index, name)
    room = parse_export(text, index, name)
    room.channel = "kakao"
    return room


def is_empty(room: Room) -> bool:
    return not room.messages and not room.events
