"""REST + SSE endpoints.

API keys are never returned by any endpoint — `/api/settings` reports only
whether a provider is *configured*.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse

from ..agents import registry
from ..config import get_settings
from ..models.schemas import DebateRequest
from ..services.logging import get_logger
from .service import DebateService, ValidationProblem, export_markdown

log = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["ai-council"])


def get_service(request: Request) -> DebateService:
    service: DebateService | None = getattr(request.app.state, "service", None)
    if service is None:  # pragma: no cover - misconfiguration
        raise HTTPException(status_code=500, detail="Service not initialised")
    return service


def rate_limited(request: Request) -> None:
    limiter = getattr(request.app.state, "limiter", None)
    if limiter is not None:
        limiter.check(request)


# --------------------------------------------------------------------------- #
# Health & settings
# --------------------------------------------------------------------------- #


@router.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok"}


@router.get("/settings")
async def get_settings_endpoint() -> dict[str, Any]:
    s = get_settings()
    return {
        "agents": registry.roster_status(s),
        "defaults": {
            "min_rounds": s.min_rounds,
            "max_rounds": s.max_rounds,
            "consensus_threshold": s.consensus_threshold,
            "timeout": s.request_timeout,
            "temperature": s.temperature,
            "max_retries": s.max_retries,
            "max_tokens": s.max_tokens,
        },
        "limits": {
            "max_question_length": s.max_question_length,
            "max_rounds_allowed": 20,
            "timeout_range": [5, 600],
            "rate_limit": {
                "requests": s.rate_limit_requests,
                "window_seconds": s.rate_limit_window_seconds,
            },
        },
        "synthesis_agent": s.synthesis_agent,
        "pricing_configured": bool(s.load_pricing().get("models")),
        "any_provider_configured": bool(registry.configured_agents(s)),
    }


# --------------------------------------------------------------------------- #
# Debates
# --------------------------------------------------------------------------- #


@router.post("/debate", status_code=status.HTTP_202_ACCEPTED)
async def start_debate(
    payload: DebateRequest,
    request: Request,
    service: DebateService = Depends(get_service),
    _: None = Depends(rate_limited),
) -> dict[str, Any]:
    try:
        debate_id, config = await service.start_debate(payload)
    except ValidationProblem as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "debate_id": debate_id,
        "status": "RUNNING",
        "config": config.model_dump(mode="json"),
        "events_url": f"/api/debate/{debate_id}/events",
    }


@router.get("/debates")
async def list_debates(
    limit: int = Query(50, ge=1, le=200),
    service: DebateService = Depends(get_service),
) -> dict[str, Any]:
    items = await service.list_debates(limit=limit)
    return {"debates": [i.model_dump(mode="json") for i in items]}


@router.delete("/debates")
async def clear_debates(service: DebateService = Depends(get_service)) -> dict[str, Any]:
    deleted = await service.clear_all()
    return {"deleted": deleted}


@router.get("/debate/{debate_id}")
async def get_debate(
    debate_id: str, service: DebateService = Depends(get_service)
) -> dict[str, Any]:
    detail = await service.get_detail(debate_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Debate not found")
    data = detail.model_dump(mode="json")
    data["running"] = service.is_running(debate_id)
    return data


@router.delete("/debate/{debate_id}")
async def delete_debate(
    debate_id: str, service: DebateService = Depends(get_service)
) -> dict[str, Any]:
    ok = await service.delete(debate_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Debate not found")
    return {"deleted": debate_id}


@router.post("/debate/{debate_id}/cancel")
async def cancel_debate(
    debate_id: str, service: DebateService = Depends(get_service)
) -> dict[str, Any]:
    cancelled = await service.cancel(debate_id)
    return {"cancelled": cancelled}


@router.get("/debate/{debate_id}/rounds")
async def get_rounds(
    debate_id: str, service: DebateService = Depends(get_service)
) -> dict[str, Any]:
    rounds = await service.get_rounds(debate_id)
    if rounds is None:
        raise HTTPException(status_code=404, detail="Debate not found")
    return {"rounds": [r.model_dump(mode="json") for r in rounds]}


@router.get("/debate/{debate_id}/export")
async def export_debate(
    debate_id: str,
    fmt: str = Query("markdown", pattern="^(markdown|md|json)$"),
    service: DebateService = Depends(get_service),
) -> Response:
    detail = await service.get_detail(debate_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Debate not found")

    if fmt == "json":
        body = json.dumps(detail.model_dump(mode="json"), ensure_ascii=False, indent=2)
        return Response(
            content=body,
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="ai-council-{debate_id}.json"'
            },
        )

    return Response(
        content=export_markdown(detail),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="ai-council-{debate_id}.md"'
        },
    )


# --------------------------------------------------------------------------- #
# Live updates (SSE)
# --------------------------------------------------------------------------- #


@router.get("/debate/{debate_id}/events")
async def stream_events(
    debate_id: str, request: Request, service: DebateService = Depends(get_service)
) -> Response:
    detail = await service.get_detail(debate_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Debate not found")

    replay = await service.replay_events(debate_id)
    running = service.is_running(debate_id)

    async def generator() -> AsyncIterator[bytes]:
        yield b": connected\n\n"
        try:
            if not running:
                for event in replay:
                    yield _sse(event.type, event.to_dict())
                yield _sse("stream_end", {"debate_id": debate_id, "replay": True})
                return

            async for event in service.bus.stream(debate_id, replay=replay):
                if await request.is_disconnected():
                    break
                if event.type == "ping":
                    yield b": ping\n\n"
                    continue
                yield _sse(event.type, event.to_dict())
                if event.type in {"debate_finished", "debate_error"}:
                    break
            yield _sse("stream_end", {"debate_id": debate_id, "replay": False})
        except Exception as exc:  # noqa: BLE001 - close the stream cleanly
            log.exception("SSE stream failed for %s", debate_id)
            yield _sse("stream_error", {"error": str(exc)})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event_type: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {payload}\n\n".encode("utf-8")


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


async def validation_problem_handler(
    _request: Request, exc: ValidationProblem
) -> JSONResponse:  # pragma: no cover - wired in main
    return JSONResponse(status_code=400, content={"detail": str(exc)})
