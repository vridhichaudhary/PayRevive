from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.ingestion.schemas import EventInput, PaymentInput
from app.ingestion.service import create_payment, get_payment, ingest_event

router = APIRouter(prefix="/v1/organizations/{org_id}/payments", tags=["local simulator"])


@router.post("")
def create(org_id: UUID, body: PaymentInput, request: Request):
    payment, duplicate = create_payment(request.app.state.engine, org_id, body)
    return JSONResponse(
        status_code=200 if duplicate else 201,
        content=jsonable_encoder({**payment, "duplicate": duplicate}),
    )


@router.post("/{payment_id}/events")
def ingest(org_id: UUID, payment_id: UUID, body: EventInput, request: Request):
    result = ingest_event(request.app.state.engine, org_id, payment_id, body)
    return JSONResponse(
        status_code=200 if result["duplicate"] else 201, content=jsonable_encoder(result)
    )


@router.get("/{payment_id}")
def detail(org_id: UUID, payment_id: UUID, request: Request):
    return get_payment(request.app.state.engine, org_id, payment_id)
