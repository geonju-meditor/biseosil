"""비서 출력 JSON 스키마. 모든 비서 응답은 이 모델로 검증한다."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    message_id: str = Field(description="근거 메시지 ID (예: s1-m3)")
    quote: str = Field(description="해당 메시지에서 그대로 옮긴 짧은 인용")
    # 검토 비서가 원문에서 채운다
    room: str | None = None
    sender: str | None = None
    when: str | None = None
    text: str | None = None


class RoomBrief(BaseModel):
    room: str
    topic: str
    priority: Literal["high", "mid", "low"] = "mid"
    why: str = ""


class LeadPlan(BaseModel):
    summary: str
    me: str | None = None
    rooms: list[RoomBrief] = []
    todo_brief: str = ""
    schedule_brief: str = ""
    reply_brief: str = ""
    warnings: list[str] = []


class TodoItem(BaseModel):
    title: str
    room: str
    due: str | None = Field(default=None, description="YYYY-MM-DD 또는 null")
    due_text: str | None = None
    priority: Literal["high", "mid", "low"] = "mid"
    evidence: Evidence


class TodoResult(BaseModel):
    """할 일 비서 결과: 내 할 일 + 팀 업무 장부(담당·결정·담당자 없는 일)."""

    items: list[TodoItem] = []
    assignments: list["Assignment"] = []
    decisions: list["Decision"] = []
    open_questions: list[str] = Field(default=[], description="아직 아무도 정하지 않은 결정·담당")


class ScheduleEvent(BaseModel):
    title: str
    room: str
    date: str = Field(description="YYYY-MM-DD")
    start: str | None = Field(default=None, description="HH:MM")
    end: str | None = None
    location: str | None = None
    my_attendance: Literal["confirmed", "requested", "unknown"] = "unknown"
    evidence: Evidence


class ScheduleResult(BaseModel):
    events: list[ScheduleEvent] = []
    conflicts: list[str] = []


class ReplyDebt(BaseModel):
    direction: Literal["i_owe", "they_owe"]
    room: str
    counterpart: str
    topic: str
    draft: str | None = None
    evidence: Evidence


class ReplyResult(BaseModel):
    debts: list[ReplyDebt] = []


class Assignment(BaseModel):
    owner: str = Field(description="담당자 이름, 정해지지 않았으면 '미정'")
    task: str
    room: str
    status: Literal["assigned", "done", "unassigned"] = "assigned"
    evidence: Evidence


class Decision(BaseModel):
    decision: str
    room: str
    evidence: Evidence


TodoResult.model_rebuild()
