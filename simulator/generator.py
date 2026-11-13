"""Seeded population with coupled, explicitly hypothetical failure mechanisms."""

import hashlib
import json
import random
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from simulator.config import GENERATOR_VERSION, GeneratorConfig, month_start
from simulator.models import (
    Attempt,
    CardMetadata,
    Customer,
    Dataset,
    Features,
    Incident,
    Label,
    Payment,
)


def canonical(value) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode()


def random_stream(seed: int, key: str) -> random.Random:
    # Streams are keyed by semantic entity, not iteration order or output filename.
    digest = hashlib.sha256(f"payrevive:{GENERATOR_VERSION}:{seed}:{key}".encode()).digest()
    return random.Random(int.from_bytes(digest, "big"))


def dataset_identity(config: GeneratorConfig, version: str = GENERATOR_VERSION) -> str:
    return hashlib.sha256(
        canonical({"version": version, **config.model_dump(mode="json")})
    ).hexdigest()[:16]


def generate(config: GeneratorConfig) -> Dataset:
    dataset_id = dataset_identity(config)
    start = datetime.combine(config.start, datetime.min.time(), UTC)
    incident_specs = [
        (10, 3, "issuer", "issuer_a"),
        (20, 2, "gateway", "gateway_y"),
        (40, 2, "issuer", "issuer_c"),
    ]
    incidents = tuple(
        Incident(
            incident_id=f"incident_{i}",
            scope=scope,
            target=target,
            starts_at=start + timedelta(days=day),
            ends_at=start + timedelta(days=day + duration),
            added_failure_hazard=0.35 * config.incident_strength,
        )
        for i, (day, duration, scope, target) in enumerate(incident_specs)
        if config.incident_strength > 0
    )
    customers, methods, payments = [], [], []
    liquidity = {}
    for index in range(config.customers):
        rng = random_stream(config.seed, f"customer:{index}")
        segment = rng.choices(["budget", "standard", "premium"], weights=[0.35, 0.5, 0.15])[0]
        amount = rng.choice(
            {
                "budget": [19900, 39900, 59900],
                "standard": [79900, 99900, 149900],
                "premium": [199900, 299900, 499900],
            }[segment]
        )
        customer_id = f"cust_{dataset_id}_{index:05d}"
        method_id = f"method_{dataset_id}_{index:05d}"
        issuer = rng.choices(["issuer_a", "issuer_b", "issuer_c"], weights=[0.4, 0.35, 0.25])[0]
        network = rng.choices(
            ["network_v", "network_m"],
            weights={"issuer_a": [0.8, 0.2], "issuer_b": [0.3, 0.7], "issuer_c": [0.5, 0.5]}[
                issuer
            ],
        )[0]
        expiry_month = month_start(config.start, rng.randint(-2, 24))
        expiry_end = month_start(expiry_month, 1) - timedelta(days=1)
        customer = Customer(
            customer_id=customer_id,
            org_id=config.org_id,
            segment=segment,
            tenure_months=rng.randint(1, 48),
            billing_day=rng.randint(1, 28),
            recurring_amount_minor=amount,
        )
        customers.append(customer)
        methods.append(
            CardMetadata(
                method_id=method_id,
                org_id=config.org_id,
                customer_id=customer_id,
                issuer=issuer,
                network=network,
                expires_on=expiry_end,
                simulator_ref=f"simref_{dataset_id}_{index:05d}",
            )
        )
        # Private heterogeneity is not serialized into observable customer features.
        liquidity[customer_id] = rng.uniform(0.6, 1.5)
        for month in range(config.months):
            prng = random_stream(config.seed, f"payment:{index}:{month}")
            month_date = month_start(config.start, month)
            due_at = datetime(
                month_date.year,
                month_date.month,
                customer.billing_day,
                prng.randint(5, 20),
                prng.randint(0, 59),
                tzinfo=UTC,
            )
            # Recurrence is stable, with occasional plan upgrades; not independent random amounts.
            amount_minor = amount * (2 if prng.random() < 0.06 else 1)
            payments.append(
                Payment(
                    payment_ref=f"pay_{dataset_id}_{index:05d}_{month}",
                    org_id=config.org_id,
                    customer_id=customer_id,
                    method_id=method_id,
                    amount_minor=amount_minor,
                    due_at=due_at,
                )
            )
    payments.sort(key=lambda p: (p.due_at, p.payment_ref))
    customer_map = {c.customer_id: c for c in customers}
    method_map = {m.method_id: m for m in methods}
    histories = defaultdict(list)
    attempts, features, labels = [], [], []
    for payment in payments:
        # Remove the config-dependent ID prefix so incident-strength variants share randomness.
        key = payment.payment_ref.split("_", 2)[2]
        rng = random_stream(config.seed, f"outcome:{key}")
        customer, method = customer_map[payment.customer_id], method_map[payment.method_id]
        gateway = rng.choices(["gateway_x", "gateway_y"], weights=[0.6, 0.4])[0]
        days_to_expiry = (method.expires_on - payment.due_at.date()).days
        ratio = payment.amount_minor / customer.recurring_amount_minor
        hazards = {
            "insufficient_funds": (
                0.02
                + (0.065 if customer.segment == "budget" else 0.015)
                + (0.04 if payment.due_at.day >= 23 else 0)
                + 0.025 * (ratio - 1)
            )
            * liquidity[customer.customer_id],
            "expired_method": 0.55
            if days_to_expiry < 0
            else (0.035 if days_to_expiry < 30 else 0.003),
            "issuer_unavailable": 0.012 if method.issuer == "issuer_c" else 0.007,
            "gateway_error": 0.01,
            "risk_block": 0.008 + 0.04 * (ratio - 1),
            "unknown": 0.012,
        }
        exposed = [
            incident
            for incident in incidents
            if incident.starts_at <= payment.due_at < incident.ends_at
            and (method.issuer if incident.scope == "issuer" else gateway) == incident.target
        ]
        for incident in exposed:
            hazards["issuer_unavailable" if incident.scope == "issuer" else "gateway_error"] += (
                incident.added_failure_hazard
            )
        failed = rng.random() < min(sum(hazards.values()), 0.95)
        category = rng.choices(list(hazards), weights=list(hazards.values()))[0] if failed else None
        disclosed = category if failed and rng.random() < config.reason_disclosure_rate else None
        # Long-tail non-incident delays overlap degraded latency: not a perfect incident label.
        latency = max(
            20,
            int(
                rng.gauss(400 + 350 * bool(exposed), 200)
                + (rng.uniform(300, 1000) if rng.random() < 0.04 else 0)
            ),
        )
        source_event_id = f"evt_{payment.payment_ref}"
        prior = histories[customer.customer_id]
        features.append(
            Features(
                payment_ref=payment.payment_ref,
                source_event_id=source_event_id,
                org_id=config.org_id,
                observed_at=payment.due_at,
                amount_minor=payment.amount_minor,
                segment=customer.segment,
                tenure_months=customer.tenure_months,
                issuer=method.issuer,
                network=method.network,
                gateway=gateway,
                days_to_expiry=days_to_expiry,
                day_of_month=payment.due_at.day,
                hour_utc=payment.due_at.hour,
                gateway_latency_ms=latency,
                prior_payment_count=len(prior),
                prior_success_rate=sum(prior) / len(prior) if prior else None,
            )
        )
        attempts.append(
            Attempt(
                payment_ref=payment.payment_ref,
                org_id=config.org_id,
                source_event_id=source_event_id,
                account=gateway,
                status="failed" if failed else "succeeded",
                reason=disclosed,
                occurred_at=payment.due_at,
            )
        )
        labels.append(
            Label(
                payment_ref=payment.payment_ref,
                source_event_id=source_event_id,
                true_failure_category=category,
                exposed_incident_ids=tuple(i.incident_id for i in exposed),
            )
        )
        prior.append(not failed)
    return Dataset(
        dataset_id=dataset_id,
        customers=tuple(customers),
        methods=tuple(methods),
        payments=tuple(payments),
        attempts=tuple(attempts),
        features=tuple(features),
        labels=tuple(labels),
        incidents=incidents,
    )
