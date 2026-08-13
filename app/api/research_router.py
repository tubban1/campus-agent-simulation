from fastapi import APIRouter, Request
from app.research_models import CalibrationObservationRequest


router = APIRouter(tags=["research"])


@router.post("/api/research/calibration-observations")
def calibration_observation(payload: CalibrationObservationRequest, request: Request):
    return request.app.state.create_calibration_observation(payload)


@router.get("/api/research/calibration-report")
def calibration_report(request: Request):
    return request.app.state.get_calibration_report()
