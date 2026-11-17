#!/usr/bin/env python3

import json
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

STATE_FILE = (
    PROJECT_ROOT
    / "reports"
    / "self_healing"
    / "incident-state.json"
)

COOLDOWN_SECONDS = 300


def utc_now():
    return datetime.now(timezone.utc)


def load_state():

    if not STATE_FILE.exists():
        return {"incidents": {}}

    try:
        return json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

    except Exception:
        return {"incidents": {}}


def save_state(state):

    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    STATE_FILE.write_text(
        json.dumps(
            state,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def get_incident_state(
    incident_id,
):

    state = load_state()

    return state.get(
        "incidents",
        {},
    ).get(
        incident_id
    )


def should_process(
    incident_id,
):

    entry = get_incident_state(
        incident_id
    )

    if not entry:
        return True

    timestamp = entry.get(
        "last_attempt_at"
    )

    if not timestamp:
        return True

    try:

        last_attempt = (
            datetime.fromisoformat(
                timestamp
            )
        )

    except ValueError:
        return True

    elapsed = (
        utc_now()
        - last_attempt
    ).total_seconds()

    return (
        elapsed
        >= COOLDOWN_SECONDS
    )


def record_processed(
    incident,
):

    state = load_state()

    incidents = state.setdefault(
        "incidents",
        {},
    )

    incidents[
        incident.incident_id
    ] = {
        "last_attempt_at":
            utc_now().isoformat(),

        "last_status":
            incident.status,

        "issue_type":
            incident.issue_type,

        "source":
            incident.source,

        "target":
            incident.target,
    }

    save_state(
        state
    )


def clear_inactive(
    active_ids,
):

    state = load_state()

    incidents = state.setdefault(
        "incidents",
        {},
    )

    active_ids = set(
        active_ids
    )

    stale_ids = [
        incident_id
        for incident_id
        in incidents
        if incident_id
        not in active_ids
    ]

    for incident_id in stale_ids:
        del incidents[
            incident_id
        ]

    save_state(
        state
    )
