"""모델에 보내기 전 개인정보 마스킹과 프롬프트 인젝션 의심 문장 감지 (결정적 규칙)."""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("전화번호", re.compile(r"(?<!\d)01[016789][-\s.]?\d{3,4}[-\s.]?\d{4}(?!\d)")),
    ("이메일", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("주민등록번호", re.compile(r"(?<!\d)\d{6}[-\s]?[1-4]\d{6}(?!\d)")),
    ("계좌번호", re.compile(r"(?<!\d)\d{2,6}-\d{2,6}-\d{2,8}(?:-\d{1,4})?(?!\d)")),
]

QUARANTINED = "[격리됨: 지시문으로 의심되는 메시지라 내용을 비서에게 보내지 않았습니다]"

_INJECTION = re.compile(
    r"(이전|앞의|위의|기존)\s*(의\s*)?(모든\s*)?(지시|명령|규칙|프롬프트)[^\n]{0,12}(무시|잊)"
    r"|시스템\s*(안내|메시지|프롬프트)\s*[:：]"
    r"|ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts)"
    r"|system\s*prompt|you\s+are\s+now|역할을\s*바꿔|너는\s*이제",
    re.IGNORECASE,
)


def mask_pii(text: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for label, pattern in _PATTERNS:
        text, n = pattern.subn(f"[{label} 가림]", text)
        if n:
            counts[label] = counts.get(label, 0) + n
    return text, counts


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION.search(text))
