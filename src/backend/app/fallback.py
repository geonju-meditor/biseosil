"""규칙 기반 대체 비서 (`AGENT_PROVIDER=deterministic` 또는 Copilot 연결 불가 시).

모델 없이도 끝까지 결과를 보여 주기 위한 보수적인 규칙이다. 인용은 원문 일부를 그대로
옮기므로 검토 비서의 근거 검증을 그대로 통과하고, 날짜는 일정 비서와 같은 도구로 계산한다.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from .dates import resolve_korean_date, weekday_name
from .kakao import CalendarEntry, Message, Room
from .schemas import (
    Assignment,
    Decision,
    Evidence,
    LeadPlan,
    ReplyDebt,
    ReplyResult,
    RoomBrief,
    ScheduleEvent,
    ScheduleResult,
    TodoItem,
    TodoResult,
)

Emit = Callable[[str, Any], Awaitable[None]]

_REQUEST = re.compile(r"부탁|주세요|주실\s*수|가능할까요|가능하실까요|해\s*주|회신|제출|검토|이수|확인\s*바랍")
_PROMISE = re.compile(r"(하|올리|드리|보내|정리하|챙기|받아보|해보)(겠습니다|ㄹ게요|릴게요|할게요|볼게요|게요)|할게요|볼게요|올릴게")
_QUESTION = re.compile(r"\?|까요|나요|세요\?|아세요|인가요")
_WHO = re.compile(r"누가")
_DECIDED = re.compile(r"확정|결정됐|결정되었|정했")
_MEETING = re.compile(r"(?<!\S)(?:(?:[가-힣A-Za-z]+)\s){0,2}[가-힣A-Za-z]*(?:회의|미팅|리허설|행사|발표|워크숍|면담)")
_TIME = re.compile(r"(?P<ampm>오전|오후)?\s*(?P<h>\d{1,2})\s*시(?:\s*(?P<mi>\d{1,2})\s*분|\s*(?P<half>반))?")
_DATE_EXPR = re.compile(
    r"(?:\d{4}\s*년\s*)?\d{1,2}\s*월\s*\d{1,2}\s*일|오늘|내일|모레|(?:이번\s*주|다음\s*주|다음주|이번주|담주)?\s*[월화수목금토일]요일"
)
_DATE_WORDS = {"오늘", "내일", "모레", "이번주", "다음주", "담주", "오전", "오후"}
_MEMO_TASK = re.compile(r"해야|하기|할\s*것|준비|보내기|작성|제출|정리|챙기|까지")
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


def _given(me: str | None) -> str | None:
    return me[1:] if me and len(me) == 3 else None


def _to_me(room: Room, message: Message, me: str | None) -> bool:
    if room.channel in ("mail", "text"):
        return True
    if not me:
        return False
    given = _given(me)
    return me in message.text or bool(given and given in message.text) or "대리님" in message.text


def _is_me(sender: str, me: str | None) -> bool:
    return bool(me) and sender.strip() == me


def _sentence(text: str, pattern: re.Pattern[str]) -> str:
    for line in text.split("\n"):
        if line.startswith("[제목]"):
            continue
        for sentence in _SENTENCE.findall(line):
            if pattern.search(sentence):
                return sentence.strip()
    return text.split("\n")[-1].strip()


def _quote(sentence: str) -> str:
    return sentence[:60]


def _subject(message: Message) -> str | None:
    first = message.text.split("\n", 1)[0]
    if first.startswith("[제목]"):
        return re.sub(r"\[[^\]]*\]\s*", "", first[4:]).strip() or None
    return None


def _clean_title(sentence: str, me: str | None) -> str:
    title = sentence
    for prefix in filter(None, [me, _given(me)]):
        title = re.sub(rf"^{re.escape(prefix)}\s*(대리님|님|씨)?[,\s]*", "", title)
    title = re.sub(r"^(네\s+)?(팀장님|대리님)[,\s]*", "", title)
    return title.strip(" ,.")[:40] or sentence[:40]


def _due(text: str, ref: str) -> tuple[str | None, str | None]:
    match = _DATE_EXPR.search(text)
    if not match:
        return None, None
    resolved = resolve_korean_date(match.group(0), ref)
    return (resolved if resolved != "unknown" else None), match.group(0).strip()


def _evidence(message: Message, sentence: str) -> Evidence:
    return Evidence(message_id=message.id, quote=_quote(sentence))


def _usable(rooms: list[Room], flagged: set[str]) -> list[tuple[Room, Message]]:
    return [(r, m) for r in rooms for m in r.messages if m.id not in flagged]


def lead(rooms: list[Room], me: str | None, flagged: set[str]) -> LeadPlan:
    briefs = []
    for room in rooms:
        if room.channel == "calendar":
            continue
        to_me = sum(1 for m in room.messages if m.id not in flagged and _to_me(room, m, me) and not _is_me(m.sender, me))
        briefs.append(
            RoomBrief(
                room=room.name,
                topic=f"메시지 {len(room.messages)}개 중 나에게 온 것 {to_me}개",
                priority="high" if to_me >= 2 else "mid" if to_me else "low",
                why="규칙 기반 판단(대체 모드)",
            )
        )
    count = sum(len(r.messages) for r in rooms)
    warnings = [f"{mid} 메시지는 지시문처럼 보여 분석에서 제외했습니다." for mid in sorted(flagged)]
    return LeadPlan(
        summary=f"대체 모드: 출처 {len(briefs)}곳의 메시지 {count}개를 규칙으로 정리했습니다. AI 비서보다 놓치는 항목이 있을 수 있습니다.",
        me=me,
        rooms=briefs,
        todo_brief="나에게 온 요청과 내가 한 약속",
        schedule_brief="날짜와 시각이 함께 있는 회의·미팅",
        reply_brief="나에게 온 질문 중 아직 답하지 않은 것",
        warnings=warnings,
    )


def todo(rooms: list[Room], me: str | None, flagged: set[str], ref: str) -> TodoResult:
    result = TodoResult()
    for room, message in _usable(rooms, flagged):
        mine = _is_me(message.sender, me)
        if mine and _PROMISE.search(message.text) and "참석" not in message.text:
            sentence = _sentence(message.text, _PROMISE)
            due, due_text = _due(message.text, message.date)
            result.items.append(
                TodoItem(title=_clean_title(sentence, me), room=room.name, due=due, due_text=due_text, priority="mid", evidence=_evidence(message, sentence))
            )
        elif not mine and _to_me(room, message, me) and (
            _REQUEST.search(message.text) or (room.channel == "text" and _MEMO_TASK.search(message.text))
        ):
            sentence = _sentence(message.text, _REQUEST if _REQUEST.search(message.text) else _MEMO_TASK)
            due, due_text = _due(sentence, message.date)
            title = _subject(message) or _clean_title(sentence, me)
            result.items.append(
                TodoItem(
                    title=title,
                    room=room.name,
                    due=due,
                    due_text=due_text,
                    priority="high" if due and due <= _plus(ref, 2) else "mid",
                    evidence=_evidence(message, sentence),
                )
            )
        if not mine and _PROMISE.search(message.text) and room.channel == "kakao":
            sentence = _sentence(message.text, _PROMISE)
            result.assignments.append(Assignment(owner=message.sender, task=_clean_title(sentence, me), room=room.name, evidence=_evidence(message, sentence)))
        if _WHO.search(message.text) and _QUESTION.search(message.text):
            sentence = _sentence(message.text, _WHO)
            result.assignments.append(
                Assignment(owner="미정", task=sentence[:40], room=room.name, status="unassigned", evidence=_evidence(message, sentence))
            )
            result.open_questions.append(f"{room.name}: {sentence}")
        if _DECIDED.search(message.text):
            sentence = _sentence(message.text, _DECIDED)
            result.decisions.append(Decision(decision=sentence[:60], room=room.name, evidence=_evidence(message, sentence)))
    return result


def _plus(ref: str, days: int) -> str:
    from datetime import date, timedelta

    try:
        return (date.fromisoformat(ref) + timedelta(days=days)).isoformat()
    except ValueError:
        return ref


def _hhmm(match: re.Match[str]) -> str:
    h = int(match["h"])
    if match["ampm"] == "오후" and h < 12 or (not match["ampm"] and 1 <= h <= 7):
        h += 12
    minute = 30 if match["half"] else int(match["mi"] or 0)
    return f"{h:02d}:{minute:02d}"


async def schedule(rooms: list[Room], me: str | None, flagged: set[str], calendar: list[CalendarEntry], emit: Emit) -> ScheduleResult:
    result = ScheduleResult()
    for room, message in _usable(rooms, flagged):
        sentence = _sentence(message.text, _TIME)
        meeting, time_match = _MEETING.search(sentence), _TIME.search(sentence)
        date_match = _DATE_EXPR.search(sentence) or _DATE_EXPR.search(message.text)
        if not (meeting and time_match and date_match):
            continue
        expression = date_match.group(0).strip()
        resolved = resolve_korean_date(expression, message.date)
        label = f"{resolved} ({weekday_name(resolved)})" if resolved != "unknown" else "계산 불가"
        await emit("tool", {"tool": "resolve_korean_date", "input": expression, "reference": message.date, "output": label})
        if resolved == "unknown":
            continue
        words = [w for w in meeting.group(0).split() if w not in _DATE_WORDS and not re.search(r"요일$", w)]
        title = " ".join(words) or meeting.group(0)
        start = _hhmm(time_match)
        if any(e.date == resolved and e.start == start and _same_title(title, e.title) for e in calendar):
            continue
        if _is_me(message.sender, me):
            attendance = "confirmed"
        elif re.search(r"가능하실까요|참석\s*부탁|오실|참석해", message.text):
            attendance = "requested"
        else:
            attendance = "unknown"
        place = re.search(r"\d+층\s*[가-힣]*(?:회의실|홀)|[가-힣]+(?:회의실|홀|스튜디오)", message.text)
        result.events.append(
            ScheduleEvent(
                title=title,
                room=room.name,
                date=resolved,
                start=start,
                location=place.group(0) if place else None,
                my_attendance=attendance,
                evidence=_evidence(message, sentence),
            )
        )
    return result


def _same_title(a: str, b: str) -> bool:
    a, b = a.replace(" ", ""), b.replace(" ", "")
    return a in b or b in a


def reply(rooms: list[Room], me: str | None, flagged: set[str]) -> ReplyResult:
    result = ReplyResult()
    for room in rooms:
        if room.channel == "text":
            continue
        usable = [m for m in room.messages if m.id not in flagged]
        for position, message in enumerate(usable):
            if _is_me(message.sender, me):
                continue
            later_mine = any(_is_me(m.sender, me) for m in usable[position + 1 :])
            if _to_me(room, message, me) and (_QUESTION.search(message.text) or (room.channel == "mail" and _REQUEST.search(message.text))):
                if later_mine and room.channel == "kakao":
                    continue
                pattern = _QUESTION if _QUESTION.search(message.text) else _REQUEST
                sentence = _sentence(message.text, pattern)
                topic = _subject(message) or _clean_title(sentence, me)
                result.debts.append(
                    ReplyDebt(
                        direction="i_owe",
                        room=room.name,
                        counterpart=message.sender,
                        topic=topic,
                        draft=f"{message.sender}님, 말씀하신 '{topic}' 확인했습니다. [답변 내용] 정리해서 [언제]까지 회신드리겠습니다.",
                        evidence=_evidence(message, sentence),
                    )
                )
            elif room.channel == "kakao" and _PROMISE.search(message.text):
                sentence = _sentence(message.text, _PROMISE)
                topic = _clean_title(sentence, me)
                result.debts.append(
                    ReplyDebt(
                        direction="they_owe",
                        room=room.name,
                        counterpart=message.sender,
                        topic=topic,
                        draft=f"{message.sender}님, '{topic}' 진행 상황 공유 부탁드려요!",
                        evidence=_evidence(message, sentence),
                    )
                )
    return result
