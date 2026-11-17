#!/usr/bin/env python3

import json
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

REPORT_DIR = (
    PROJECT_ROOT
    / "reports"
    / "self_healing"
)

LATEST_REPORT = (
    REPORT_DIR
    / "incidents-latest.json"
)


def utc_timestamp():

    return datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%d-%H%M%S"
    )


def build_payload(incidents):

    return {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),

        "count": len(incidents),

        "incidents": [
            incident.to_dict()
            for incident in incidents
        ],
    }


def save_incidents(
    incidents,
    history_incidents=None,
):

    REPORT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    # -----------------------------------------------------
    # Current state
    #
    # Always overwrite this file.
    # The Web UI uses it to determine whether there are
    # CURRENT active/escalated incidents.
    # -----------------------------------------------------

    latest_payload = build_payload(
        incidents
    )

    LATEST_REPORT.write_text(
        json.dumps(
            latest_payload,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    # -----------------------------------------------------
    # Historical documentation
    #
    # Only incidents which were actually processed during
    # this cycle should create a historical record.
    # Cooldown-skipped incidents are excluded.
    # -----------------------------------------------------

    if history_incidents is None:
        history_incidents = incidents

    if not history_incidents:
        return None

    history_payload = build_payload(
        history_incidents
    )

    history_file = (
        REPORT_DIR
        / (
            "incidents-"
            + utc_timestamp()
            + ".json"
        )
    )

    history_file.write_text(
        json.dumps(
            history_payload,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    return history_file