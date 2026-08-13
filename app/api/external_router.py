from fastapi import APIRouter, Request


router = APIRouter(tags=["external-information"])


@router.post("/api/external-information/sync")
def sync_external_information(request: Request):
    return request.app.state.sync_external_information()


@router.get("/api/external-information")
def external_information(request: Request):
    return request.app.state.get_external_information()
