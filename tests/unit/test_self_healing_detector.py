import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(
    0,
    str(ROOT / "automation" / "self_healing"),
)


from detector import (
    detect_interface_faults,
    detect_unreachable_devices,
)


def test_healthy_network_has_no_interface_incident():

    topology = {
        "nodes": [
            {
                "id": "R6",
                "role": "Router",
            },
            {
                "id": "R7",
                "role": "Router",
            },
        ],

        "edges": [
            {
                "source": "R6",
                "target": "R7",

                "source_interface":
                    "Ethernet2",

                "target_interface":
                    "Ethernet1",

                "source_state":
                    "up",

                "target_state":
                    "up",

                "cable_id":
                    24,
            }
        ],
    }

    incidents = detect_interface_faults(
        topology
    )

    assert incidents == []


def test_down_interface_creates_incident():

    topology = {
        "nodes": [
            {
                "id": "R6",
                "role": "Router",
            },
            {
                "id": "R7",
                "role": "Router",
            },
        ],

        "edges": [
            {
                "source": "R6",
                "target": "R7",

                "source_interface":
                    "Ethernet2",

                "target_interface":
                    "Ethernet1",

                "source_state":
                    "down",

                "target_state":
                    "down",

                "cable_id":
                    24,
            }
        ],
    }

    incidents = detect_interface_faults(
        topology
    )

    assert len(incidents) == 1

    incident = incidents[0]

    assert (
        incident.incident_id
        == "interface-down-cable-24"
    )

    assert (
        incident.issue_type
        == "interface_down"
    )

    assert (
        incident.source
        == "R6"
    )

    assert (
        incident.target
        == "R7"
    )


def test_unknown_state_is_ignored():

    topology = {
        "nodes": [
            {
                "id": "R6",
                "role": "Router",
            },
            {
                "id": "R7",
                "role": "Router",
            },
        ],

        "edges": [
            {
                "source": "R6",
                "target": "R7",

                "source_interface":
                    "Ethernet2",

                "target_interface":
                    "Ethernet1",

                "source_state":
                    "unknown",

                "target_state":
                    "down",

                "cable_id":
                    24,
            }
        ],
    }

    incidents = detect_interface_faults(
        topology
    )

    assert incidents == []


def test_host_link_is_ignored():

    topology = {
        "nodes": [
            {
                "id": "R7",
                "role": "Router",
            },
            {
                "id": "H5",
                "role": "Host",
            },
        ],

        "edges": [
            {
                "source": "R7",
                "target": "H5",

                "source_interface":
                    "Ethernet2",

                "target_interface":
                    "eth1",

                "source_state":
                    "down",

                "target_state":
                    "unknown",
            }
        ],
    }

    incidents = detect_interface_faults(
        topology
    )

    assert incidents == []


def test_unreachable_device_is_ignored_by_interface_detector():

    topology = {
        "nodes": [
            {
                "id": "R6",
                "role": "Router",
            },
            {
                "id": "R7",
                "role": "Router",
            },
        ],

        "edges": [
            {
                "source": "R6",
                "target": "R7",

                "source_interface":
                    "Ethernet2",

                "target_interface":
                    "Ethernet1",

                "source_state":
                    "down",

                "target_state":
                    "down",

                "cable_id":
                    24,
            }
        ],
    }

    incidents = detect_interface_faults(
        topology,
        ignore_devices={
            "R6"
        },
    )

    assert incidents == []


@patch(
    "detector.ping_device"
)
@patch(
    "detector.get_network_devices"
)
def test_unreachable_device_creates_incident(
    mock_devices,
    mock_ping,
):

    mock_devices.return_value = [
        {
            "text": "R6",
            "value": "172.20.20.16",
        }
    ]

    mock_ping.return_value = False

    incidents = (
        detect_unreachable_devices()
    )

    assert len(incidents) == 1

    incident = incidents[0]

    assert (
        incident.incident_id
        == "device-unreachable-R6"
    )

    assert (
        incident.issue_type
        == "device_unreachable"
    )

    assert (
        incident.source_state
        == "unreachable"
    )


@patch(
    "detector.ping_device"
)
@patch(
    "detector.get_network_devices"
)
def test_reachable_device_creates_no_incident(
    mock_devices,
    mock_ping,
):

    mock_devices.return_value = [
        {
            "text": "R6",
            "value": "172.20.20.16",
        }
    ]

    mock_ping.return_value = True

    incidents = (
        detect_unreachable_devices()
    )

    assert incidents == []
