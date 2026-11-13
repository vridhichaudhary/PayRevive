"""Project only supported operational records into the simulator API. Never read truth."""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

import httpx
from app.ingestion.schemas import EventInput, PaymentInput

from simulator.models import Attempt, Payment


def load_operational(directory: Path) -> tuple[list[Payment], list[Attempt]]:
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("synthetic_only") is not True:
        raise ValueError("Only synthetic manifests are accepted")
    records = []
    for name, model in [("payments", Payment), ("attempts", Attempt)]:
        relative = f"operational/{name}.jsonl"
        content = (directory / relative).read_bytes()
        if hashlib.sha256(content).hexdigest() != manifest["files"].get(relative):
            raise ValueError(f"Checksum mismatch: {relative}")
        records.append([model.model_validate_json(line) for line in content.splitlines()])
    payments, attempts = records
    if len({p.payment_ref for p in payments}) != len(payments):
        raise ValueError("Duplicate payment references in artifact")
    by_ref = {p.payment_ref: p for p in payments}
    if len(attempts) != len(payments) or len({a.payment_ref for a in attempts}) != len(attempts):
        raise ValueError("Expected one initial attempt per payment")
    if len({(a.org_id, a.provider, a.account, a.source_event_id) for a in attempts}) != len(
        attempts
    ):
        raise ValueError("Duplicate source identities in artifact")
    for attempt in attempts:
        payment = by_ref.get(attempt.payment_ref)
        if (
            payment is None
            or payment.org_id != attempt.org_id
            or payment.due_at != attempt.occurred_at
        ):
            raise ValueError("Operational relationship mismatch")
        # Validate all projected inputs before the first HTTP write.
        payment_payload(payment)
        event_payload(attempt)
    return payments, attempts


def payment_payload(payment: Payment) -> dict:
    return PaymentInput(
        reference=payment.payment_ref, amount_minor=payment.amount_minor, currency=payment.currency
    ).model_dump(mode="json", exclude_none=True)


def event_payload(attempt: Attempt) -> dict:
    return EventInput(**attempt.model_dump(exclude={"payment_ref", "org_id"})).model_dump(
        mode="json"
    )


def ingest(directory: Path, client: httpx.Client, limit: int = 100) -> dict[str, int]:
    if urlparse(str(client.base_url)).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("This adapter is restricted to a local simulator API")
    if limit <= 0:
        raise ValueError("Limit must be positive")
    payments, attempts = load_operational(directory)
    by_ref = {a.payment_ref: a for a in attempts}
    counts = {
        "payments_created": 0,
        "payments_replayed": 0,
        "events_created": 0,
        "events_replayed": 0,
    }
    for payment in payments[:limit]:
        path = f"/v1/organizations/{payment.org_id}/payments"
        response = client.post(path, json=payment_payload(payment))
        response.raise_for_status()
        counts["payments_created" if response.status_code == 201 else "payments_replayed"] += 1
        payment_id = response.json()["id"]
        response = client.post(
            f"{path}/{payment_id}/events", json=event_payload(by_ref[payment.payment_ref])
        )
        response.raise_for_status()
        counts["events_created" if response.status_code == 201 else "events_replayed"] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest operational synthetic records into local API"
    )
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    with httpx.Client(base_url=args.api, timeout=15, follow_redirects=False) as client:
        print(json.dumps(ingest(args.dataset, client, args.limit), sort_keys=True))


if __name__ == "__main__":
    main()
