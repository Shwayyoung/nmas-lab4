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
    process_incident,
    process_interface_incident,
)


def build_interface_incident():

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


def build_device_incident():

    return Incident(
        incident_id="device-unreachable-R6",
        issue_type="device_unreachable",
        status="DETECTED",

        source="R6",
        source_interface="Management",
        source_state="unreachable",
    )


# ---------------------------------------------------------
# Interface healing success
# ---------------------------------------------------------

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

    incident = build_interface_incident()

    mock_diagnose.return_value = {
        "device": "R6",
        "interface": "Ethernet2",
        "cause": "administrative_shutdown",
    }

    mock_verify.return_value = True

    process_interface_incident(
        incident
    )

    assert (
        incident.status
        == "HEALED"
    )

    mock_diagnose.assert_called_once()

    mock_remediate.assert_called_once()

    mock_verify.assert_called_once()


# ---------------------------------------------------------
# No supported diagnosis
# ---------------------------------------------------------

@patch(
    "engine.diagnose_interface_incident"
)
def test_no_supported_diagnosis_escalates(
    mock_diagnose,
):

    incident = build_interface_incident()

    mock_diagnose.return_value = None

    process_interface_incident(
        incident
    )

    assert (
        incident.status
        == "ESCALATED"
    )

    assert any(
        "No supported automatic remediation"
        in step
        for step
        in incident.troubleshooting_steps
    )


# ---------------------------------------------------------
# Remediation failure
# ---------------------------------------------------------

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

    incident = build_interface_incident()

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

    assert any(
        "Automatic remediation failed"
        in action
        for action
        in incident.remediation_actions
    )


# ---------------------------------------------------------
# Verification failure
# ---------------------------------------------------------

@patch(
    "engine.verify_incident_cleared"
)
@patch(
    "engine.remediate_interface_shutdown"
)
@patch(
    "engine.diagnose_interface_incident"
)
def test_verification_failure_escalates(
    mock_diagnose,
    mock_remediate,
    mock_verify,
):

    incident = build_interface_incident()

    mock_diagnose.return_value = {
        "device": "R6",
        "interface": "Ethernet2",
        "cause": "administrative_shutdown",
    }

    mock_verify.return_value = False

    process_interface_incident(
        incident
    )

    assert (
        incident.status
        == "ESCALATED"
    )

    assert any(
        "did not restore"
        in result
        for result
        in incident.verification
    )


# ---------------------------------------------------------
# Verification exception
# ---------------------------------------------------------

@patch(
    "engine.verify_incident_cleared"
)
@patch(
    "engine.remediate_interface_shutdown"
)
@patch(
    "engine.diagnose_interface_incident"
)
def test_verification_exception_escalates(
    mock_diagnose,
    mock_remediate,
    mock_verify,
):

    incident = build_interface_incident()

    mock_diagnose.return_value = {
        "device": "R6",
        "interface": "Ethernet2",
        "cause": "administrative_shutdown",
    }

    mock_verify.side_effect = RuntimeError(
        "Telemetry unavailable"
    )

    process_interface_incident(
        incident
    )

    assert (
        incident.status
        == "ESCALATED"
    )

    assert any(
        "Verification failed"
        in result
        for result
        in incident.verification
    )


# ---------------------------------------------------------
# Device unreachable + SSH fails
# ---------------------------------------------------------

@patch(
    "engine.verify_ssh_reachability"
)
def test_device_ssh_failure_escalates(
    mock_ssh,
):

    incident = build_device_incident()

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
        "SSH connection failed"
        in step
        for step
        in incident.troubleshooting_steps
    )

    assert any(
        "could not be performed"
        in action
        for action
        in incident.remediation_actions
    )


# ---------------------------------------------------------
# ICMP fails but SSH succeeds
# ---------------------------------------------------------

@patch(
    "engine.verify_ssh_reachability"
)
def test_device_ssh_success_becomes_healed(
    mock_ssh,
):

    incident = build_device_incident()

    mock_ssh.return_value = True

    process_device_unreachable(
        incident
    )

    assert (
        incident.status
        == "HEALED"
    )

    assert any(
        "SSH management connectivity succeeded"
        in step
        for step
        in incident.troubleshooting_steps
    )


# ---------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------

@patch(
    "engine.process_interface_incident"
)
def test_dispatch_interface_incident(
    mock_process,
):

    incident = build_interface_incident()

    process_incident(
        incident
    )

    mock_process.assert_called_once_with(
        incident
    )


@patch(
    "engine.process_device_unreachable"
)
def test_dispatch_device_incident(
    mock_process,
):

    incident = build_device_incident()

    process_incident(
        incident
    )

    mock_process.assert_called_once_with(
        incident
    )


# ---------------------------------------------------------
# Unsupported incident type
# ---------------------------------------------------------

def test_unsupported_incident_escalates():

    incident = Incident(
        incident_id="unsupported-test",
        issue_type="unknown_problem",
        status="DETECTED",
        source="R6",
    )

    process_incident(
        incident
    )

    assert (
        incident.status
        == "ESCALATED"
    )

    assert any(
        "Unsupported incident type"
        in step
        for step
        in incident.troubleshooting_steps
    )
