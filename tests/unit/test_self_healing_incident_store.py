import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(
    0,
    str(ROOT / "automation" / "self_healing"),
)


import incident_store
from incident import Incident


def build_incident(
    status="HEALED",
):

    return Incident(
        incident_id="interface-down-cable-24",
        issue_type="interface_down",
        status=status,

        source="R6",
        source_interface="Ethernet2",
        source_state="down",

        target="R7",
        target_interface="Ethernet1",
        target_state="down",

        cable_id=24,
    )


def test_latest_report_is_always_written(
    tmp_path,
    monkeypatch,
):

    report_dir = (
        tmp_path
        / "self_healing"
    )

    latest_report = (
        report_dir
        / "incidents-latest.json"
    )

    monkeypatch.setattr(
        incident_store,
        "REPORT_DIR",
        report_dir,
    )

    monkeypatch.setattr(
        incident_store,
        "LATEST_REPORT",
        latest_report,
    )

    incident = build_incident()

    incident_store.save_incidents(
        [incident],
        history_incidents=[],
    )

    assert (
        latest_report.exists()
    )

    data = json.loads(
        latest_report.read_text()
    )

    assert data["count"] == 1

    assert (
        data["incidents"][0]["status"]
        == "HEALED"
    )


def test_processed_incident_creates_history(
    tmp_path,
    monkeypatch,
):

    report_dir = (
        tmp_path
        / "self_healing"
    )

    latest_report = (
        report_dir
        / "incidents-latest.json"
    )

    monkeypatch.setattr(
        incident_store,
        "REPORT_DIR",
        report_dir,
    )

    monkeypatch.setattr(
        incident_store,
        "LATEST_REPORT",
        latest_report,
    )

    incident = build_incident()

    history_file = (
        incident_store.save_incidents(
            [incident],
            history_incidents=[
                incident
            ],
        )
    )

    assert history_file is not None

    assert history_file.exists()

    data = json.loads(
        history_file.read_text()
    )

    assert data["count"] == 1

    assert (
        data["incidents"][0]["incident_id"]
        == "interface-down-cable-24"
    )


def test_cooldown_skipped_incident_does_not_create_history(
    tmp_path,
    monkeypatch,
):

    report_dir = (
        tmp_path
        / "self_healing"
    )

    latest_report = (
        report_dir
        / "incidents-latest.json"
    )

    monkeypatch.setattr(
        incident_store,
        "REPORT_DIR",
        report_dir,
    )

    monkeypatch.setattr(
        incident_store,
        "LATEST_REPORT",
        latest_report,
    )

    incident = build_incident(
        status="ESCALATED"
    )

    history_file = (
        incident_store.save_incidents(
            [incident],
            history_incidents=[],
        )
    )

    assert history_file is None

    history_files = list(
        report_dir.glob(
            "incidents-*.json"
        )
    )

    history_files = [
        path
        for path in history_files
        if path.name
        != "incidents-latest.json"
    ]

    assert history_files == []


def test_healthy_run_clears_latest(
    tmp_path,
    monkeypatch,
):

    report_dir = (
        tmp_path
        / "self_healing"
    )

    latest_report = (
        report_dir
        / "incidents-latest.json"
    )

    monkeypatch.setattr(
        incident_store,
        "REPORT_DIR",
        report_dir,
    )

    monkeypatch.setattr(
        incident_store,
        "LATEST_REPORT",
        latest_report,
    )

    incident = build_incident(
        status="ESCALATED"
    )

    incident_store.save_incidents(
        [incident],
        history_incidents=[
            incident
        ],
    )

    incident_store.save_incidents(
        [],
        history_incidents=[],
    )

    data = json.loads(
        latest_report.read_text()
    )

    assert data["count"] == 0

    assert data["incidents"] == []


def test_latest_contains_escalated_incident(
    tmp_path,
    monkeypatch,
):

    report_dir = (
        tmp_path
        / "self_healing"
    )

    latest_report = (
        report_dir
        / "incidents-latest.json"
    )

    monkeypatch.setattr(
        incident_store,
        "REPORT_DIR",
        report_dir,
    )

    monkeypatch.setattr(
        incident_store,
        "LATEST_REPORT",
        latest_report,
    )

    incident = build_incident(
        status="ESCALATED"
    )

    incident_store.save_incidents(
        [incident],
        history_incidents=[],
    )

    data = json.loads(
        latest_report.read_text()
    )

    assert (
        data["incidents"][0]["status"]
        == "ESCALATED"
    )


def test_history_keeps_final_incident_status(
    tmp_path,
    monkeypatch,
):

    report_dir = (
        tmp_path
        / "self_healing"
    )

    latest_report = (
        report_dir
        / "incidents-latest.json"
    )

    monkeypatch.setattr(
        incident_store,
        "REPORT_DIR",
        report_dir,
    )

    monkeypatch.setattr(
        incident_store,
        "LATEST_REPORT",
        latest_report,
    )

    incident = build_incident(
        status="HEALED"
    )

    incident.remediation_actions.append(
        "Enabled R6 Ethernet2."
    )

    incident.verification.append(
        "Interface telemetry returned to UP."
    )

    history_file = (
        incident_store.save_incidents(
            [incident],
            history_incidents=[
                incident
            ],
        )
    )

    data = json.loads(
        history_file.read_text()
    )

    saved = data["incidents"][0]

    assert (
        saved["status"]
        == "HEALED"
    )

    assert (
        "Enabled R6 Ethernet2."
        in saved["remediation_actions"]
    )

    assert (
        "Interface telemetry returned to UP."
        in saved["verification"]
    )
