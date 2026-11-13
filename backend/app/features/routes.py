from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.features.contracts import SnapshotInput
from app.features.service import read_snapshot, record_snapshot

router = APIRouter(
    prefix="/v1/organizations/{org_id}/payments/{payment_id}/events/{event_id}/features",
    tags=["feature provenance"],
)


@router.post("")
def create(org_id: UUID, payment_id: UUID, event_id: UUID, body: SnapshotInput, request: Request):
    row, duplicate = record_snapshot(request.app.state.engine, org_id, payment_id, event_id, body)
    return JSONResponse(status_code=200 if duplicate else 201, content=jsonable_encoder(row))


@router.get("")
def detail(org_id: UUID, payment_id: UUID, event_id: UUID, request: Request):
    return read_snapshot(request.app.state.engine, org_id, payment_id, event_id)
