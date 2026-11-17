#!/usr/bin/env python3

import sys
from pathlib import Path


SELF_HEALING_DIR = Path(__file__).resolve().parent
AUTOMATION_DIR = SELF_HEALING_DIR.parent

sys.path.insert(0, str(AUTOMATION_DIR))

from interface_control import control_interface


def remediate_interface_shutdown(
    incident,
    diagnosis,
):

    device = diagnosis["device"]
    interface = diagnosis["interface"]

    incident.remediation_actions.append(
        f"Attempting to enable "
        f"{device} {interface}."
    )

    control_interface(
        device,
        interface,
        "enable",
    )

    incident.remediation_actions.append(
        f"Enabled {device} {interface}."
    )
