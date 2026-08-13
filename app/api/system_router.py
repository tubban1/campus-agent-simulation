from fastapi import APIRouter, Request


router = APIRouter(tags=["system"])


@router.get("/api/ai/test")
def ai_test(request: Request):
    return request.app.state.ai_test()
