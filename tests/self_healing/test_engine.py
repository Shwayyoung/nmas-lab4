import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(
    0,
    str(ROOT / "automation"),
)

sys.path.insert(
    0,
    str(ROOT / "automation" / "self_healing"),
)

from incident import Incident

from engine import (
    process_device_unreachable,
    process_interface_incident,
)


def interface_incident():

    return Incident(
        incident_id="interface-down-cable-24",
        issue_type="interface_down",
        status="DETECTED",
        source="R6",
        source_interface="Ethernet2",
        source_state="down",
        target="R7",
        target_interface="Ethernet1",
        target_state="down",
        cable_id=24,
    )


@patch(
    "engine.verify_incident_cleared"
)
@patch(
    "engine.remediate_interface_shutdown"
)
@patch(
    "engine.diagnose_interface_incident"
)
def test_interface_success_becomes_healed(
    mock_diagnose,
    mock_remediate,
    mock_verify,
):

    incident = interface_incident()

    mock_diagnose.return_value = {
        "device": "R6",
        "interface": "Ethernet2",
        "cause": "administrative_shutdown",
    }

    mock_verify.return_value = True

    process_interface_incident(
        incident
    )

    assert incident.status == "HEALED"

    mock_remediate.assert_called_once()


@patch(
    "engine.remediate_interface_shutdown"
)
@patch(
    "engine.diagnose_interface_incident"
)
def test_remediation_failure_escalates(
    mock_diagnose,
    mock_remediate,
):

    incident = interface_incident()

    mock_diagnose.return_value = {
        "device": "R6",
        "interface": "Ethernet2",
        "cause": "administrative_shutdown",
    }

    mock_remediate.side_effect = RuntimeError(
        "SSH configuration failed"
    )

    process_interface_incident(
        incident
    )

    assert (
        incident.status
        == "ESCALATED"
    )


@patch(
    "engine.verify_ssh_reachability"
)
def test_unreachable_ssh_failure_escalates(
    mock_ssh,
):

    incident = Incident(
        incident_id="device-unreachable-R6",
        issue_type="device_unreachable",
        status="DETECTED",
        source="R6",
        source_interface="Management",
        source_state="unreachable",
    )

    mock_ssh.side_effect = RuntimeError(
        "Connection timed out"
    )

    process_device_unreachable(
        incident
    )

    assert (
        incident.status
        == "ESCALATED"
    )

    assert any(
        "SSH" in action
        for action
        in incident.remediation_actions
    )
