"""비서실 FastAPI 앱: 정적 화면, 샘플, 비서 워크플로 SSE, .ics 생성."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import get_settings
from .ics import IcsEvent, build_ics
from .kakao import parse_export
from .workflow import RawRoom, RunInput, Runtime, build_workflow, workflow_graph

logger = logging.getLogger("biseosil")
settings = get_settings()

BACKEND_DIR = Path(__file__).resolve().parent.parent
SAMPLES_DIR = BACKEND_DIR / "samples"
FRONTEND_DIR = next(
    (p for p in (BACKEND_DIR / "frontend", BACKEND_DIR.parent / "frontend") if p.is_dir()),
    BACKEND_DIR / "frontend",
)
SAMPLE_INFO = {
    "team-project.txt": "경영정보시스템 팀플 4조 (PC 내보내기)",
    "club-staff.txt": "사진동아리 빛담 운영진 (모바일 내보내기)",
    "dept-notice.txt": "경영학과 25학번 공지방 (지시문 끼워넣기 시도 포함)",
}
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
    return error(422, "invalid_request", "입력 형식이 올바르지 않습니다.")


@app.exception_handler(Exception)
async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("처리되지 않은 오류")
    return error(500, "internal_error", "서버에서 문제가 생겼습니다. 잠시 후 다시 시도해 주세요.")


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
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


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "copilot": state.client is not None}


@app.get("/api/samples")
async def samples() -> dict[str, Any]:
    items = []
    for name, label in SAMPLE_INFO.items():
        path = SAMPLES_DIR / name
        if path.exists():
            items.append({"file": name, "label": label, "text": path.read_text(encoding="utf-8")})
    return {"me": "이지은", "samples": items}


@app.get("/api/workflow")
async def workflow_info(rooms: int = 3) -> dict[str, Any]:
    return workflow_graph(max(1, min(rooms, settings.max_rooms)))


class RoomIn(BaseModel):
    text: str = Field(min_length=1)
    name: str | None = Field(default=None, max_length=100)


class RunRequest(BaseModel):
    rooms: list[RoomIn] = Field(min_length=1)
    me: str | None = Field(default=None, max_length=40)


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


@app.post("/api/run")
async def run(request: Request, body: RunRequest):
    if len(body.rooms) > settings.max_rooms:
        return error(400, "too_many_rooms", f"대화방은 한 번에 {settings.max_rooms}개까지 넣을 수 있습니다.")
    total = sum(len(r.text) for r in body.rooms)
    if total > settings.max_input_chars:
        return error(
            413, "input_too_long", f"입력이 너무 깁니다. 합계 {settings.max_input_chars:,}자 이하로 줄여 주세요 (현재 {total:,}자)."
        )
    for i, room in enumerate(body.rooms):
        if not parse_export(room.text, i, room.name).messages:
            label = room.name or f"{i + 1}번째 입력"
            return error(
                400,
                "unrecognized_format",
                f"'{label}'에서 카카오톡 대화를 찾지 못했습니다. 카카오톡 '대화 내보내기' txt를 그대로 넣어 주세요.",
            )
    if state.client is None:
        return error(503, "ai_unavailable", "AI 연결이 준비되지 않았습니다. 잠시 후 다시 시도해 주세요.")
    if _rate_limited(_client_ip(request)):
        return error(429, "rate_limited", "요청이 많습니다. 몇 분 뒤에 다시 시도해 주세요.")
    if state.semaphore.locked():
        return error(503, "busy", "지금 다른 분석이 많이 진행 중입니다. 잠시 후 다시 시도해 주세요.")

    run_input = RunInput(rooms=[RawRoom(text=r.text, name=r.name) for r in body.rooms], me=(body.me or "").strip() or None)
    workflow = build_workflow(Runtime(state.client, settings.copilot_model), len(run_input.rooms))

    async def stream():
        async with state.semaphore:
            started = time.monotonic()
            yield _sse({"agent": "system", "status": "start", "data": {"graph": workflow_graph(len(run_input.rooms))}})
            output: dict[str, Any] | None = None
            try:
                async with asyncio.timeout(settings.run_timeout_sec):
                    async for ev in workflow.run(run_input, stream=True):
                        if ev.type == "progress":
                            yield _sse(ev.data)
                        elif ev.type == "output":
                            output = ev.data
                        elif ev.type in ("executor_failed", "failed", "error"):
                            logger.warning("workflow event %s from %s", ev.type, getattr(ev, "executor_id", None))
            except TimeoutError:
                yield _sse({"agent": "system", "status": "error", "data": {"code": "timeout", "message": "분석 시간이 너무 오래 걸려 멈췄습니다. 대화방 수를 줄여 다시 시도해 주세요."}})
                return
            except Exception:  # noqa: BLE001
                logger.exception("워크플로 실행 실패")
                yield _sse({"agent": "system", "status": "error", "data": {"code": "workflow_failed", "message": "분석 중 문제가 생겼습니다. 다시 시도해 주세요."}})
                return
            if output is None:
                yield _sse({"agent": "system", "status": "error", "data": {"code": "no_result", "message": "결과를 만들지 못했습니다. 다시 시도해 주세요."}})
                return
            elapsed = time.monotonic() - started
            messages = sum(r["messages"] for r in output["rooms"])
            items = len(output["todos"]) + len(output["events"]) + len(output["replies"])
            manual_sec = messages * SECONDS_PER_MESSAGE + items * SECONDS_PER_ITEM
            output["stats"] = {
                "elapsedSec": round(elapsed, 1),
                "messages": messages,
                "items": items,
                "manualMin": round(manual_sec / 60),
                "formula": f"메시지 {messages}개×{SECONDS_PER_MESSAGE}초 + 항목 {items}개×{SECONDS_PER_ITEM}초",
            }
            logger.info("run done rooms=%d messages=%d items=%d elapsed=%.1f", len(output["rooms"]), messages, items, elapsed)
            yield _sse({"agent": "system", "status": "result", "data": output})

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
