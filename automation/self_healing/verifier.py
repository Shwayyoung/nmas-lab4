#!/usr/bin/env python3

import time

from detector import (
    detect_interface_faults,
    get_topology,
)


def verify_incident_cleared(
    incident,
    attempts=6,
    delay=3,
):

    for attempt in range(
        1,
        attempts + 1,
    ):

        time.sleep(delay)

        topology = get_topology()

        incidents = detect_interface_faults(
            topology
        )

        active_ids = {
            item.incident_id
            for item in incidents
        }

        if incident.incident_id not in active_ids:

            incident.verification.append(
                "Interface telemetry returned to UP."
            )

            return True

        incident.verification.append(
            f"Verification attempt {attempt}: "
            f"link is still down."
        )

    return False
