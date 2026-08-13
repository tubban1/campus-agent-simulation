from typing import Optional

from fastapi import APIRouter, Request


router = APIRouter(tags=["news"])


@router.post("/api/agents/daily-diaries/backfill")
def backfill_daily_diaries(request: Request, day: Optional[int] = None, rewrite: bool = False):
    return request.app.state.backfill_agent_daily_diaries(day, rewrite)


@router.get("/api/newspaper/today")
def newspaper_today(request: Request):
    return request.app.state.get_newspaper_today()


@router.get("/api/newspaper/agent-posts")
def agent_newspaper_posts(request: Request, day: Optional[int] = None):
    return request.app.state.get_agent_newspaper_posts(day)


@router.get("/api/newspaper/ai-today")
def ai_newspaper_today(request: Request):
    return request.app.state.get_ai_newspaper_today()
