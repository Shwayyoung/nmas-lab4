#!/usr/bin/env python3

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Incident:
    incident_id: str
    issue_type: str
    status: str

    source: str
    source_interface: str = ""
    source_state: str = ""

    target: str = ""
    target_interface: str = ""
    target_state: str = ""

    cable_id: int | None = None

    detected_at: str = field(default_factory=utc_now)

    troubleshooting_steps: list[str] = field(
        default_factory=list
    )

    remediation_actions: list[str] = field(
        default_factory=list
    )

    verification: list[str] = field(
        default_factory=list
    )

    def to_dict(self):
        return asdict(self)