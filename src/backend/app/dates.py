"""일정 비서의 함수 도구: 한국어 날짜 표현을 실제 날짜로 계산한다.

모델이 요일·날짜 계산을 틀리는 환각을 막기 위해 계산은 코드가 한다.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

_WEEKDAYS = "월화수목금토일"
_ABS = re.compile(r"(?:(?P<y>\d{4})\s*년\s*)?(?P<m>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일")
_SLASH = re.compile(r"(?<!\d)(?P<m>\d{1,2})\s*[/.]\s*(?P<d>\d{1,2})(?!\d)")
_DAY_ONLY = re.compile(r"(?<!\d)(?P<d>\d{1,2})\s*일")
_WEEKDAY = re.compile(r"(?P<which>이번\s*주|이번주|다음\s*주|다음주|다다음\s*주|담주)?\s*(?P<wd>[월화수목금토일])요일")


def _parse_iso(value: str) -> date:
    return date.fromisoformat(value.strip()[:10])


def resolve_korean_date(expression: str, reference_date: str) -> str:
    """'내일', '이번주 금요일', '다음주 화요일', '11월 12일' 같은 표현을 YYYY-MM-DD로 바꾼다.

    reference_date는 그 말이 나온 메시지의 날짜(YYYY-MM-DD)다. 계산할 수 없으면 'unknown'.
    """

    try:
        ref = _parse_iso(reference_date)
    except ValueError:
        return "unknown"
    text = expression.strip()

    if match := _ABS.search(text):
        year = int(match["y"]) if match["y"] else ref.year
        try:
            candidate = date(year, int(match["m"]), int(match["d"]))
            if not match["y"] and candidate < ref - timedelta(days=60):
                candidate = candidate.replace(year=year + 1)
        except ValueError:
            return "unknown"
        return candidate.isoformat()

    for word, delta in (("오늘", 0), ("금일", 0), ("내일", 1), ("모레", 2), ("글피", 3)):
        if word in text:
            return (ref + timedelta(days=delta)).isoformat()

    if match := _WEEKDAY.search(text):
        target = _WEEKDAYS.index(match["wd"])
        which = (match["which"] or "").replace(" ", "")
        monday = ref - timedelta(days=ref.weekday())
        if which in ("다음주", "담주"):
            monday += timedelta(days=7)
        elif which == "다다음주":
            monday += timedelta(days=14)
        candidate = monday + timedelta(days=target)
        if not which and candidate < ref:
            candidate += timedelta(days=7)
        return candidate.isoformat()

    if match := _SLASH.search(text):
        try:
            return date(ref.year, int(match["m"]), int(match["d"])).isoformat()
        except ValueError:
            return "unknown"

    if match := _DAY_ONLY.search(text):
        day = int(match["d"])
        try:
            candidate = ref.replace(day=day)
        except ValueError:
            return "unknown"
        if candidate < ref:
            month = ref.month % 12 + 1
            candidate = candidate.replace(year=ref.year + (ref.month == 12), month=month)
        return candidate.isoformat()

    return "unknown"


def weekday_name(value: str) -> str:
    try:
        return _WEEKDAYS[_parse_iso(value).weekday()] + "요일"
    except ValueError:
        return ""
