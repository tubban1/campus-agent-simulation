from fastapi import APIRouter, Request


router = APIRouter(tags=["campus"])


@router.get("/api/inventory")
def get_inventory(request: Request):
    return request.app.state.get_inventory()


@router.get("/api/campus/environment/today")
def get_today_environment(request: Request):
    return request.app.state.get_today_environment()


@router.get("/api/campus/spaces")
def get_campus_spaces(request: Request):
    return request.app.state.get_campus_spaces()
