#!/usr/bin/env python3

import json
import subprocess
from urllib.request import urlopen

from incident import Incident


TOPOLOGY_URL = (
    "http://127.0.0.1:8088/api/topology"
)

DEVICES_URL = (
    "http://127.0.0.1:8088/api/devices"
)

NETWORK_ROLES = {
    "Router",
    "Switch",
    "Multi-Layer Switch",
}


# ---------------------------------------------------------
# HTTP data sources
# ---------------------------------------------------------

def get_topology():

    with urlopen(
        TOPOLOGY_URL,
        timeout=20,
    ) as response:

        return json.loads(
            response.read().decode("utf-8")
        )


def get_network_devices():

    with urlopen(
        DEVICES_URL,
        timeout=10,
    ) as response:

        return json.loads(
            response.read().decode("utf-8")
        )


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def normalize_state(value):

    return str(
        value or "unknown"
    ).strip().lower()


def build_role_map(topology):

    return {
        node["id"]: node.get(
            "role",
            "",
        )
        for node in topology.get(
            "nodes",
            [],
        )
    }


def ping_device(ip_address):

    try:

        result = subprocess.run(
            [
                "ping",
                "-c",
                "1",
                "-W",
                "1",
                ip_address,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )

        return (
            result.returncode == 0
        )

    except (
        subprocess.TimeoutExpired,
        OSError,
    ):

        return False


# ---------------------------------------------------------
# Device reachability detection
# ---------------------------------------------------------

def detect_unreachable_devices():

    incidents = []

    devices = get_network_devices()

    for device in devices:

        device_name = device.get(
            "text"
        )

        mgmt_ip = device.get(
            "value"
        )

        if (
            not device_name
            or not mgmt_ip
        ):
            continue

        if ping_device(
            mgmt_ip
        ):
            continue

        incident = Incident(
            incident_id=(
                f"device-unreachable-"
                f"{device_name}"
            ),
            issue_type=(
                "device_unreachable"
            ),
            status="DETECTED",
            source=device_name,
            source_interface=(
                "Management"
            ),
            source_state=(
                "unreachable"
            ),
        )

        incident.troubleshooting_steps.append(
            f"Management IP {mgmt_ip} "
            f"did not respond to ICMP."
        )

        incident.troubleshooting_steps.append(
            "Management-plane reachability "
            "failure detected."
        )

        incidents.append(
            incident
        )

    return incidents


# ---------------------------------------------------------
# Interface fault detection
# ---------------------------------------------------------

def detect_interface_faults(
    topology,
    ignore_devices=None,
):

    incidents = []

    ignore_devices = set(
        ignore_devices or []
    )

    role_map = build_role_map(
        topology
    )

    for edge in topology.get(
        "edges",
        [],
    ):

        source = edge.get(
            "source"
        )

        target = edge.get(
            "target"
        )

        if (
            source in ignore_devices
            or target in ignore_devices
        ):
            continue

        source_role = role_map.get(
            source
        )

        target_role = role_map.get(
            target
        )

        # Only network-to-network links are
        # actionable for automatic healing.
        #
        # Host-facing links are ignored because
        # host telemetry frequently reports
        # UNKNOWN.
        if (
            source_role
            not in NETWORK_ROLES
            or target_role
            not in NETWORK_ROLES
        ):
            continue

        source_state = normalize_state(
            edge.get(
                "source_state"
            )
        )

        target_state = normalize_state(
            edge.get(
                "target_state"
            )
        )

        # Healthy link.
        if (
            source_state == "up"
            and target_state == "up"
        ):
            continue

        # UNKNOWN is intentionally ignored.
        #
        # A stale or unavailable telemetry value
        # should not trigger automatic changes.
        if (
            source_state == "unknown"
            or target_state == "unknown"
        ):
            continue

        # At least one side must explicitly be DOWN.
        if (
            source_state != "down"
            and target_state != "down"
        ):
            continue

        cable_id = edge.get(
            "cable_id"
        )

        if cable_id is not None:

            incident_id = (
                f"interface-down-cable-"
                f"{cable_id}"
            )

        else:

            incident_id = (
                f"interface-down-"
                f"{source}-"
                f"{target}"
            )

        incident = Incident(
            incident_id=incident_id,
            issue_type=(
                "interface_down"
            ),
            status="DETECTED",

            source=source,
            source_interface=edge.get(
                "source_interface",
                "",
            ),
            source_state=source_state,

            target=target,
            target_interface=edge.get(
                "target_interface",
                "",
            ),
            target_state=target_state,

            cable_id=cable_id,
        )

        incident.troubleshooting_steps.append(
            "Confirmed the link exists "
            "in the intended topology."
        )

        incident.troubleshooting_steps.append(
            f"Observed "
            f"{source} "
            f"{incident.source_interface}"
            f"={source_state}."
        )

        incident.troubleshooting_steps.append(
            f"Observed "
            f"{target} "
            f"{incident.target_interface}"
            f"={target_state}."
        )

        incidents.append(
            incident
        )

    return incidents


# ---------------------------------------------------------
# Combined detection
# ---------------------------------------------------------

def detect_all_faults():

    # First determine whether any entire devices
    # are unreachable.
    device_incidents = (
        detect_unreachable_devices()
    )

    unreachable_devices = {
        incident.source
        for incident
        in device_incidents
    }

    # Then inspect interface telemetry.
    #
    # Links connected to unreachable devices
    # are ignored so that one failed device does
    # not create many duplicate interface incidents.
    topology = get_topology()

    interface_incidents = (
        detect_interface_faults(
            topology,
            ignore_devices=(
                unreachable_devices
            ),
        )
    )

    return (
        device_incidents
        + interface_incidents
    )


# ---------------------------------------------------------
# Standalone test
# ---------------------------------------------------------

def main():

    incidents = (
        detect_all_faults()
    )

    print(
        f"[Detector] "
        f"{len(incidents)} "
        f"actionable incident(s)"
    )

    for incident in incidents:

        if (
            incident.issue_type
            == "device_unreachable"
        ):

            print(
                f"  "
                f"{incident.incident_id}: "
                f"{incident.source} "
                f"management unreachable"
            )

        else:

            print(
                f"  "
                f"{incident.incident_id}: "
                f"{incident.source} "
                f"{incident.source_interface}"
                f"={incident.source_state} "
                f"<-> "
                f"{incident.target} "
                f"{incident.target_interface}"
                f"={incident.target_state}"
            )


if __name__ == "__main__":
    main()