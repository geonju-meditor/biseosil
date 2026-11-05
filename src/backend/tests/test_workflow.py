"""모델 호출 없이 전체 워크플로(수집 → 팀장 → 전문 비서 → 검토)와 API를 검증한다."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import agents as A
from app.schemas import (
    Evidence,
    LeadPlan,
    LedgerResult,
    ReplyDebt,
    ReplyResult,
    ScheduleEvent,
    ScheduleResult,
    TodoItem,
    TodoResult,
)
from app.workflow import RawRoom, RunInput, Runtime, build_workflow

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def _fake_results(me_room_msg: str):
    good = Evidence(message_id=me_room_msg, quote="응 금요일까지 올릴게")
    return {
        LeadPlan: LeadPlan(summary="요약", todo_brief="할 일 지시"),
        TodoResult: TodoResult(
            items=[
                TodoItem(title="목차 초안 올리기", room="팀플", due="2026-11-06", evidence=good),
                TodoItem(title="목차 초안 올리기", room="팀플", due="2026-11-06", evidence=good),
                TodoItem(title="지어낸 일", room="팀플", evidence=Evidence(message_id=me_room_msg, quote="교수님께 꼭 장학금 서류 내기")),
                TodoItem(title="없는 메시지", room="팀플", evidence=Evidence(message_id="r9-m9", quote="없음")),
            ]
        ),
        ScheduleResult: ScheduleResult(
            events=[
                ScheduleEvent(title="회의 A", room="팀플", date="2026-11-05", start="19:00", evidence=good),
                ScheduleEvent(title="모임 B", room="팀플", date="2026-11-05", start="19:30", evidence=good),
            ]
        ),
        ReplyResult: ReplyResult(debts=[ReplyDebt(direction="i_owe", room="팀플", counterpart="김민수", topic="답", evidence=good)]),
        LedgerResult: LedgerResult(open_questions=["버스 몇 인승?"]),
    }


@pytest.fixture
def fake_agents(monkeypatch):
    rooms = sorted(SAMPLES.glob("*.txt"))
    team_index = next(i for i, p in enumerate(rooms) if p.name == "team-project.txt")
    from app.kakao import parse_export

    room = parse_export(rooms[team_index].read_text(encoding="utf-8"), team_index)
    msg = next(m for m in room.messages if "금요일까지 올릴게" in m.text)
    results = _fake_results(msg.id)

    def build_agent(*args, **kwargs):
        return object()

    async def run_json_agent(agent, prompt, schema, emit, timeout=75):
        assert "<input>" in prompt and "</input>" in prompt
        if schema is LedgerResult:
            raise A.AgentRunError("invalid_output", "장부 비서 출력 형식 오류")
        await emit("delta", {"chars": 10, "tail": "{...}"})
        return results[schema].model_copy(deep=True)

    monkeypatch.setattr(A, "build_agent", build_agent)
    monkeypatch.setattr(A, "run_json_agent", run_json_agent)
    return rooms


async def test_workflow_reviews_and_isolates_errors(fake_agents):
    rooms = [RawRoom(p.read_text(encoding="utf-8")) for p in fake_agents]
    workflow = build_workflow(Runtime(client=None), len(rooms))
    progress, output = [], None
    async for ev in workflow.run(RunInput(rooms, "이지은"), stream=True):
        if ev.type == "progress":
            progress.append(ev.data)
        elif ev.type == "output":
            output = ev.data
    assert output is not None
    agents_seen = {p["agent"] for p in progress}
    assert {"lead", "collector", "todo", "schedule", "reply", "ledger", "review"} <= agents_seen
    assert len(output["todos"]) == 1, "중복·근거 없는 항목이 제거되어야 한다"
    reasons = {r["reason"] for r in output["review"]["removed"]}
    assert "원문에서 근거를 찾지 못함" in reasons and "중복 병합" in reasons
    assert output["todos"][0]["evidence"]["sender"] == "이지은"
    assert output["review"]["conflicts"], "30분 간격 일정은 충돌로 표시"
    assert "ledger" in output["errors"], "한 비서 실패는 그 비서만 오류로 표시"
    assert output["replies"] and output["events"]
    assert any(r["flagged"] for r in output["rooms"]), "인젝션 메시지는 수집 단계에서 격리"


def test_api_run_streams_result(fake_agents):
    from app import main

    with TestClient(main.app) as client:
        main.state.client = object()
        samples = client.get("/api/samples").json()
        body = {"me": samples["me"], "rooms": [{"text": s["text"]} for s in samples["samples"]]}
        with client.stream("POST", "/api/run", json=body) as res:
            assert res.status_code == 200
            text = "".join(res.iter_text())
        assert '"status": "result"' in text and '"stats"' in text


def test_api_errors_use_common_format():
    from app import main

    with TestClient(main.app) as client:
        res = client.post("/api/run", json={"rooms": [{"text": "카톡 아님"}]})
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "unrecognized_format"
        res = client.post("/api/run", json={"rooms": [{"text": "가" * 30_001}]})
        assert res.json()["error"]["code"] == "input_too_long"
        res = client.post("/api/run", json={})
        assert res.json()["error"]["code"] == "invalid_request"
        assert client.get("/health").json()["status"] == "ok"
        ics = client.post("/api/ics", json={"events": [{"title": "회의", "date": "2026-11-05", "start": "19:00"}]})
        assert ics.status_code == 200 and "BEGIN:VEVENT" in ics.text
