"""Score already-issued alerts against separate synthetic incident truth."""

from datetime import UTC, datetime, timedelta

from ml.detection.detector import Window
from simulator.models import Incident


def evaluate(windows: list[Window], incidents: tuple[Incident, ...]) -> dict:
    matches = {incident.incident_id: [] for incident in incidents}
    incident_windows = set()
    closes = []
    for index, window in enumerate(windows):
        opened = datetime.combine(window.day, datetime.min.time(), UTC)
        closed = opened + timedelta(days=1)
        closes.append(closed)
        for incident in incidents:
            if (
                window.scope == incident.scope
                and window.target == incident.target
                and opened < incident.ends_at
                and closed > incident.starts_at
            ):
                matches[incident.incident_id].append(index)
                incident_windows.add(index)

    eligible_negative = sum(w.eligible and i not in incident_windows for i, w in enumerate(windows))
    metrics = {}
    for method in ("rolling", "fixed"):
        alerts = {i for i, w in enumerate(windows) if getattr(w, f"{method}_alert")}
        true = len(alerts & incident_windows)
        false = len(alerts - incident_windows)
        details = []
        for incident in incidents:
            overlapping = matches[incident.incident_id]
            detected = alerts.intersection(overlapping)
            first = min((closes[i] for i in detected), default=None)
            details.append(
                {
                    "incident_id": incident.incident_id,
                    "scope": incident.scope,
                    "target": incident.target,
                    "starts_at": incident.starts_at.isoformat(),
                    "ends_at": incident.ends_at.isoformat(),
                    "eligible_windows": sum(windows[i].eligible for i in overlapping),
                    "first_alert_at": first.isoformat() if first else None,
                    "delay_hours": (first - incident.starts_at).total_seconds() / 3600
                    if first
                    else None,
                }
            )
        detected_count = sum(r["first_alert_at"] is not None for r in details)
        metrics[method] = {
            "alerts": len(alerts),
            "true_alert_windows": true,
            "false_alert_windows": false,
            "alert_precision": true / len(alerts) if alerts else None,
            "incident_recall": detected_count / len(incidents) if incidents else None,
            "incidents_detected": detected_count,
            "false_alert_rate": false / eligible_negative if eligible_negative else None,
            "incidents": details,
        }
    return {
        "eligible_windows": sum(w.eligible for w in windows),
        "total_windows": len(windows),
        "eligible_negative_windows": eligible_negative,
        "incident_count": len(incidents),
        "methods": metrics,
    }
