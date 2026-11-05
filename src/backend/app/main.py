"""비서실 FastAPI 앱: 정적 화면, 샘플, 비서 워크플로 SSE, .ics 생성."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from ag_ui.core import (
    BaseEvent,
    CustomEvent,
    EventType,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StepFinishedEvent,
    StepStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)

from .channels import CHANNEL_LABEL, detect_channel, is_empty, parse_input
from .config import get_settings
from .ics import IcsEvent, build_ics
from .workflow import RawInput, RunInput, Runtime, build_workflow, workflow_graph

logger = logging.getLogger("biseosil")
settings = get_settings()

BACKEND_DIR = Path(__file__).resolve().parent.parent
SAMPLES_DIR = BACKEND_DIR / "samples"
FRONTEND_DIR = next(
    (p for p in (BACKEND_DIR / "frontend", BACKEND_DIR.parent / "frontend") if p.is_dir()),
    BACKEND_DIR / "frontend",
)
SAMPLE_ME = "김하늘"
SAMPLE_INFO = [
    ("kakao-marketing.txt", "kakao", "마케팅팀 업무방", "카카오톡 PC 내보내기"),
    ("kakao-launch-tf.txt", "kakao", "신제품 런칭 TF", "카카오톡 모바일 내보내기 · 지시문 끼워넣기 포함"),
    ("mail-inbox.txt", "mail", "받은 메일함", "업무 메일 4통 붙여넣기 · 스팸 지시문 포함"),
    ("calendar.ics", "calendar", "내 캘린더", "기존 일정 .ics"),
]
SECONDS_PER_MESSAGE = 6
SECONDS_PER_ITEM = 45


def _setup_telemetry() -> None:
    if not settings.applicationinsights_connection_string:
        return
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        from agent_framework.observability import enable_instrumentation

        configure_azure_monitor(
            connection_string=settings.applicationinsights_connection_string,
            logger_name="biseosil",
        )
        enable_instrumentation(enable_sensitive_data=False)
        logger.info("Application Insights 연결됨")
    except Exception:  # noqa: BLE001
        logger.exception("Application Insights 설정 실패")


class AppState:
    client: Any = None
    client_error: str | None = None
    semaphore: asyncio.Semaphore
    hits: dict[str, deque[float]]


state = AppState()


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(level=logging.INFO)
    _setup_telemetry()
    state.semaphore = asyncio.Semaphore(settings.max_concurrent_runs)
    state.hits = defaultdict(deque)
    try:
        from copilot import CopilotClient

        state.client = CopilotClient()
        await state.client.start()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Copilot 클라이언트 시작 실패")
        state.client, state.client_error = None, type(exc).__name__
    yield
    if state.client is not None:
        try:
            await state.client.stop()
        except Exception:  # noqa: BLE001
            pass


app = FastAPI(title="비서실 Biseosil", lifespan=lifespan)


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return error(exc.status_code, "http_error", "요청을 처리할 수 없습니다." if exc.status_code != 404 else "찾을 수 없습니다.")


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return error(400, "invalid_request", "입력 형식이 올바르지 않습니다.")


@app.exception_handler(Exception)
async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("처리되지 않은 오류")
    return error(500, "internal_error", "서버에서 문제가 생겼습니다. 잠시 후 다시 시도해 주세요.")


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # 인그레스가 맨 뒤에 덧붙인 값만 믿는다. 앞의 값은 클라이언트가 위조할 수 있다.
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    hits = state.hits[ip]
    while hits and now - hits[0] > settings.rate_limit_window_sec:
        hits.popleft()
    if len(hits) >= settings.rate_limit_runs:
        return True
    hits.append(now)
    return False


def _runtime() -> Runtime:
    return Runtime(state.client, settings.copilot_model, settings.agent_provider)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "copilot": state.client is not None, "provider": _runtime().mode}


@app.get("/api/samples")
async def samples() -> dict[str, Any]:
    items = []
    for file, channel, name, label in SAMPLE_INFO:
        path = SAMPLES_DIR / file
        if path.exists():
            items.append({"file": file, "channel": channel, "name": name, "label": label, "text": path.read_text(encoding="utf-8")})
    return {"me": SAMPLE_ME, "samples": items}


@app.get("/api/workflow")
async def workflow_info(inputs: int = 4) -> dict[str, Any]:
    count = max(1, min(inputs, settings.max_inputs))
    return workflow_graph(["kakao"] * count)


class SourceIn(BaseModel):
    text: str = Field(min_length=1)
    name: str | None = Field(default=None, max_length=100)
    channel: Literal["kakao", "mail", "calendar", "text"] | None = None
    label: str | None = Field(default=None, max_length=100, description="오류 문구에 쓸 표시 이름(선택)")


class RunRequest(BaseModel):
    inputs: list[SourceIn] = Field(min_length=1)
    me: str | None = Field(default=None, max_length=40)


def _sse(event: BaseEvent) -> bytes:
    return f"data: {event.model_dump_json(by_alias=True, exclude_none=True)}\n\n".encode()


def _progress_events(data: dict[str, Any]) -> list[BaseEvent]:
    """워크플로 진행 이벤트를 AG-UI 이벤트로 바꾼다."""

    node, status = data["node"], data["status"]
    if status == "tool":
        call_id = f"tool-{uuid.uuid4().hex[:8]}"
        tool = data["data"]
        args = {"expression": tool["input"], "reference_date": tool["reference"]}


        return [
            ToolCallStartEvent(type=EventType.TOOL_CALL_START, tool_call_id=call_id, tool_call_name=tool["tool"], parent_message_id=node),
            ToolCallArgsEvent(type=EventType.TOOL_CALL_ARGS, tool_call_id=call_id, delta=json.dumps(args, ensure_ascii=False)),
            ToolCallEndEvent(type=EventType.TOOL_CALL_END, tool_call_id=call_id),
            ToolCallResultEvent(
                type=EventType.TOOL_CALL_RESULT, message_id=f"{call_id}-result", tool_call_id=call_id, content=tool["output"], role="tool"
            ),
        ]
    events: list[BaseEvent] = []
    if status == "start":
        events.append(StepStartedEvent(type=EventType.STEP_STARTED, step_name=node))
    events.append(CustomEvent(type=EventType.CUSTOM, name="agent.progress", value=data))
    if status in ("done", "error"):
        events.append(StepFinishedEvent(type=EventType.STEP_FINISHED, step_name=node))
    return events


@app.post("/api/run")
async def run(request: Request, body: RunRequest):
    if len(body.inputs) > settings.max_inputs:
        return error(400, "too_many_inputs", f"입력은 한 번에 {settings.max_inputs}개까지 넣을 수 있습니다.")
    total = sum(len(i.text) for i in body.inputs)
    if total > settings.max_input_chars:
        return error(
            413, "input_too_long", f"입력이 너무 깁니다. 합계 {settings.max_input_chars:,}자 이하로 줄여 주세요 (현재 {total:,}자)."
        )
    channels: list[str] = []
    unnamed = 0
    for i, item in enumerate(body.inputs):
        unnamed += not item.name
        channel = item.channel or detect_channel(item.text)
        channels.append(channel)
        if is_empty(parse_input(item.text, i, item.name, channel)):
            label = item.name or item.label or f"붙여넣은 내용 {unnamed}"
            hint = {
                "kakao": "카카오톡 '대화 내보내기' txt를 그대로 넣어 주세요.",
                "mail": "메일은 '보낸 사람/날짜/제목' 머리글이 있는 본문이나 .eml 파일을 넣어 주세요.",
                "calendar": "캘린더는 BEGIN:VEVENT가 있는 .ics 파일을 넣어 주세요.",
                "text": "내용을 한 줄 이상 넣어 주세요.",
            }[channel]
            return error(400, "unrecognized_format", f"'{label}'에서 {CHANNEL_LABEL[channel]} 내용을 찾지 못했습니다. {hint}")
    if all(c == "calendar" for c in channels):
        return error(400, "invalid_request", "캘린더만으로는 분석할 메시지가 없습니다. 카카오톡·메일·메모를 함께 넣어 주세요.")
    if _rate_limited(_client_ip(request)):
        return error(429, "rate_limited", "요청이 많습니다. 몇 분 뒤에 다시 시도해 주세요.")
    if state.semaphore.locked():
        return error(503, "busy", "지금 다른 분석이 많이 진행 중입니다. 잠시 후 다시 시도해 주세요.")

    run_input = RunInput(
        inputs=[RawInput(text=i.text, name=i.name, channel=c) for i, c in zip(body.inputs, channels, strict=True)],
        me=(body.me or "").strip() or None,
    )
    runtime = _runtime()
    workflow = build_workflow(runtime, len(run_input.inputs))
    thread_id, run_id = f"thread-{uuid.uuid4().hex[:12]}", f"run-{uuid.uuid4().hex[:12]}"

    def fail(code: str, message: str) -> bytes:
        return _sse(RunErrorEvent(type=EventType.RUN_ERROR, code=code, message=message))

    async def stream():
        async with state.semaphore:
            started = time.monotonic()
            yield _sse(RunStartedEvent(type=EventType.RUN_STARTED, thread_id=thread_id, run_id=run_id))
            graph = workflow_graph(channels) | {"mode": runtime.mode}
            yield _sse(CustomEvent(type=EventType.CUSTOM, name="workflow.graph", value=graph))
            output: dict[str, Any] | None = None
            try:
                async with asyncio.timeout(settings.run_timeout_sec):
                    async for ev in workflow.run(run_input, stream=True):
                        if ev.type == "progress":
                            for event in _progress_events(ev.data):
                                yield _sse(event)
                        elif ev.type == "output":
                            output = ev.data
                        elif ev.type in ("executor_failed", "failed", "error"):
                            logger.warning("workflow event %s from %s", ev.type, getattr(ev, "executor_id", None))
            except TimeoutError:
                yield fail("timeout", "분석 시간이 너무 오래 걸려 멈췄습니다. 입력 수를 줄여 다시 시도해 주세요.")
                return
            except Exception:  # noqa: BLE001
                logger.exception("워크플로 실행 실패")
                yield fail("workflow_failed", "분석 중 문제가 생겼습니다. 다시 시도해 주세요.")
                return
            if output is None:
                yield fail("no_result", "결과를 만들지 못했습니다. 다시 시도해 주세요.")
                return
            elapsed = time.monotonic() - started
            messages = sum(i["messages"] for i in output["inputs"])
            # 같은 원문에서 나온 항목은 한 번만 센다(손으로 정리해도 한 번 처리하므로).
            items = len(
                {(t["evidence"]["message_id"]) for t in output["todos"]}
                | {(e["evidence"]["message_id"]) for e in output["events"]}
                | {(r["evidence"]["message_id"]) for r in output["replies"]}
            )
            manual_sec = messages * SECONDS_PER_MESSAGE + items * SECONDS_PER_ITEM
            stats = {
                "elapsedSec": round(elapsed, 1),
                "messages": messages,
                "items": items,
                "manualMin": round(manual_sec / 60),
                "formula": f"메시지 {messages}개×{SECONDS_PER_MESSAGE}초 + 항목 {items}개×{SECONDS_PER_ITEM}초",
            }
            output["stats"] = stats
            logger.info(
                "run done mode=%s inputs=%d messages=%d items=%d elapsed=%.1f",
                output["mode"], len(output["inputs"]), messages, items, elapsed,
            )
            yield _sse(CustomEvent(type=EventType.CUSTOM, name="workflow.result", value=output))
            if output.get("lead") and output["lead"].get("summary"):
                message_id = f"msg-{uuid.uuid4().hex[:8]}"
                yield _sse(TextMessageStartEvent(type=EventType.TEXT_MESSAGE_START, message_id=message_id, role="assistant"))
                yield _sse(TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, message_id=message_id, delta=output["lead"]["summary"]))
                yield _sse(TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=message_id))
            yield _sse(RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=thread_id, run_id=run_id, result={"stats": stats}))

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class IcsRequest(BaseModel):
    events: list[IcsEvent] = Field(min_length=1, max_length=50)


@app.post("/api/ics")
async def ics(body: IcsRequest):
    try:
        content = build_ics(body.events)
    except ValueError as exc:
        return error(400, "invalid_event", str(exc))
    return Response(
        content=content,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="biseosil.ics"'},
    )


if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")
