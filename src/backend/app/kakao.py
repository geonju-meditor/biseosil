"""카카오톡 '대화 내보내기' txt 파서 (PC·모바일 형식).

수집 비서가 사용하는 결정적 도구다. 원문을 메시지 단위로 나누고 각 메시지에
`s{입력}-m{번호}` 형태의 ID를 붙여, 다른 비서가 근거를 ID로 인용할 수 있게 한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

_TITLE = re.compile(r"^(?P<room>.+?)\s*(?:님과)?\s*카카오톡 대화\s*$")
_PC_DAY = re.compile(r"^-+\s*(?P<y>\d{4})년\s*(?P<m>\d{1,2})월\s*(?P<d>\d{1,2})일.*-+\s*$")
_PC_MSG = re.compile(r"^\[(?P<sender>[^\]]+)\]\s*\[(?P<ampm>오전|오후)\s*(?P<h>\d{1,2}):(?P<mi>\d{2})\]\s?(?P<text>.*)$")
_MOBILE_MSG = re.compile(
    r"^(?P<y>\d{4})년\s*(?P<m>\d{1,2})월\s*(?P<d>\d{1,2})일\s*(?P<ampm>오전|오후)\s*(?P<h>\d{1,2}):(?P<mi>\d{2}),\s*"
    r"(?P<sender>[^:]+?)\s*:\s?(?P<text>.*)$"
)
_MOBILE_DOT_MSG = re.compile(
    r"^(?P<y>\d{4})\.\s*(?P<m>\d{1,2})\.\s*(?P<d>\d{1,2})\.\s*(?P<ampm>오전|오후)\s*(?P<h>\d{1,2}):(?P<mi>\d{2}),\s*"
    r"(?P<sender>[^:]+?)\s*:\s?(?P<text>.*)$"
)
_SAVED = re.compile(r"^저장한 날짜\s*:")
_SKIP_TEXT = {"사진", "동영상", "이모티콘", "삭제된 메시지입니다."}


@dataclass
class Message:
    id: str
    sender: str
    date: str  # YYYY-MM-DD
    time: str  # HH:MM
    text: str

    def prompt_line(self) -> str:
        flat = self.text.replace("\n", " / ")
        return f"[{self.id}] {self.date} {self.time} {self.sender}: {flat}"


@dataclass
class CalendarEntry:
    id: str
    title: str
    date: str  # YYYY-MM-DD
    start: str | None  # HH:MM, 종일이면 None
    end: str | None
    location: str | None = None

    def prompt_line(self) -> str:
        span = f"{self.start}~{self.end or ''}" if self.start else "종일"
        where = f" ({self.location})" if self.location else ""
        return f"[{self.id}] {self.date} {span} {self.title}{where}"


@dataclass
class Room:
    """입력 하나(카톡 방, 메일 묶음, 캘린더)."""

    index: int
    name: str
    messages: list[Message] = field(default_factory=list)
    channel: str = "kakao"
    events: list[CalendarEntry] = field(default_factory=list)

    @property
    def participants(self) -> list[str]:
        seen: dict[str, None] = {}
        for message in self.messages:
            seen.setdefault(message.sender, None)
        return list(seen)

    @property
    def last_date(self) -> str | None:
        return self.messages[-1].date if self.messages else None


def _to_24h(ampm: str, hour: str, minute: str) -> str:
    h = int(hour) % 12
    if ampm == "오후":
        h += 12
    return f"{h:02d}:{minute}"


def parse_export(raw: str, index: int, fallback_name: str | None = None) -> Room:
    lines = raw.replace("\r\n", "\n").replace("\ufeff", "").split("\n")
    name = fallback_name or f"대화방 {index + 1}"
    current_day: str | None = None
    room = Room(index=index, name=name)
    last: Message | None = None

    for position, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if position < 3 and (title := _TITLE.match(stripped)):
            room.name = title.group("room").strip() or name
            continue
        if _SAVED.match(stripped):
            continue
        if day := _PC_DAY.match(stripped):
            current_day = date(int(day["y"]), int(day["m"]), int(day["d"])).isoformat()
            continue
        match = _PC_MSG.match(stripped)
        msg_day = current_day
        if not match:
            match = _MOBILE_MSG.match(stripped) or _MOBILE_DOT_MSG.match(stripped)
            if match:
                msg_day = date(int(match["y"]), int(match["m"]), int(match["d"])).isoformat()
        if match:
            text = match["text"].strip()
            if text in _SKIP_TEXT or not msg_day:
                last = None
                continue
            last = Message(
                id=f"s{index + 1}-m{len(room.messages) + 1}",
                sender=match["sender"].strip(),
                date=msg_day,
                time=_to_24h(match["ampm"], match["h"], match["mi"]),
                text=text,
            )
            room.messages.append(last)
        elif last is not None:
            # 여러 줄 메시지의 이어지는 줄
            last.text = f"{last.text}\n{stripped}"
    return room


def guess_me(rooms: list[Room], hint: str | None) -> str | None:
    """사용자가 이름을 주지 않으면 여러 방에 공통으로 등장하는 사람을 '나'로 추정한다."""

    if hint and hint.strip():
        return hint.strip()
    counts: dict[str, int] = {}
    rooms = [r for r in rooms if r.channel == "kakao"]
    for room in rooms:
        for person in room.participants:
            counts[person] = counts.get(person, 0) + 1
    if len(rooms) < 2 or not counts:
        return None
    best = max(counts.values())
    shared = [person for person, count in counts.items() if count == best]
    return shared[0] if best >= 2 and len(shared) == 1 else None
