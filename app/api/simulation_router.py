from fastapi import APIRouter, Request


router = APIRouter(tags=["simulation"])


@router.post("/api/simulate/ai-day/progress")
def start_ai_day_progress(request: Request):
    return request.app.state.start_simulate_ai_day_progress()


@router.post("/api/simulate/ai-day/stream")
def stream_ai_day(request: Request):
    return request.app.state.simulate_ai_day_stream()


@router.get("/api/simulate/ai-day/progress/{job_id}")
def get_ai_day_progress(job_id: str, request: Request, after: int = 0):
    return request.app.state.get_simulation_progress(job_id, after)
