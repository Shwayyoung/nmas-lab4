import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(
    0,
    str(ROOT / "automation" / "self_healing"),
)


import incident_state


def test_first_incident_should_process(
    tmp_path,
    monkeypatch,
):

    state_file = (
        tmp_path
        / "incident-state.json"
    )

    monkeypatch.setattr(
        incident_state,
        "STATE_FILE",
        state_file,
    )

    result = incident_state.should_process(
        "interface-down-cable-24"
    )

    assert result is True


def test_recent_incident_is_in_cooldown(
    tmp_path,
    monkeypatch,
):

    state_file = (
        tmp_path
        / "incident-state.json"
    )

    monkeypatch.setattr(
        incident_state,
        "STATE_FILE",
        state_file,
    )

    now = datetime.now(
        timezone.utc
    )

    state = {
        "incidents": {
            "interface-down-cable-24": {
                "last_attempt_at":
                    now.isoformat(),

                "last_status":
                    "ESCALATED",

                "issue_type":
                    "interface_down",

                "source":
                    "R6",

                "target":
                    "R7",
            }
        }
    }

    incident_state.save_state(
        state
    )

    result = incident_state.should_process(
        "interface-down-cable-24"
    )

    assert result is False


def test_expired_cooldown_should_process(
    tmp_path,
    monkeypatch,
):

    state_file = (
        tmp_path
        / "incident-state.json"
    )

    monkeypatch.setattr(
        incident_state,
        "STATE_FILE",
        state_file,
    )

    old_time = (
        datetime.now(
            timezone.utc
        )
        - timedelta(
            seconds=incident_state.COOLDOWN_SECONDS + 10
        )
    )

    state = {
        "incidents": {
            "interface-down-cable-24": {
                "last_attempt_at":
                    old_time.isoformat(),

                "last_status":
                    "ESCALATED",

                "issue_type":
                    "interface_down",

                "source":
                    "R6",

                "target":
                    "R7",
            }
        }
    }

    incident_state.save_state(
        state
    )

    result = incident_state.should_process(
        "interface-down-cable-24"
    )

    assert result is True


def test_record_processed_saves_state(
    tmp_path,
    monkeypatch,
):

    from incident import Incident

    state_file = (
        tmp_path
        / "incident-state.json"
    )

    monkeypatch.setattr(
        incident_state,
        "STATE_FILE",
        state_file,
    )

    incident = Incident(
        incident_id="interface-down-cable-24",
        issue_type="interface_down",
        status="HEALED",
        source="R6",
        source_interface="Ethernet2",
        source_state="down",
        target="R7",
        target_interface="Ethernet1",
        target_state="down",
        cable_id=24,
    )

    incident_state.record_processed(
        incident
    )

    saved = (
        incident_state.get_incident_state(
            "interface-down-cable-24"
        )
    )

    assert saved is not None

    assert (
        saved["last_status"]
        == "HEALED"
    )

    assert (
        saved["issue_type"]
        == "interface_down"
    )

    assert (
        saved["source"]
        == "R6"
    )

    assert (
        saved["target"]
        == "R7"
    )


def test_clear_inactive_removes_recovered_incident(
    tmp_path,
    monkeypatch,
):

    state_file = (
        tmp_path
        / "incident-state.json"
    )

    monkeypatch.setattr(
        incident_state,
        "STATE_FILE",
        state_file,
    )

    now = datetime.now(
        timezone.utc
    ).isoformat()

    state = {
        "incidents": {

            "interface-down-cable-24": {
                "last_attempt_at": now,
                "last_status": "ESCALATED",
                "issue_type": "interface_down",
                "source": "R6",
                "target": "R7",
            },

            "device-unreachable-R8": {
                "last_attempt_at": now,
                "last_status": "ESCALATED",
                "issue_type": "device_unreachable",
                "source": "R8",
                "target": "",
            },
        }
    }

    incident_state.save_state(
        state
    )

    # R8 remains active.
    # R6 has recovered.
    incident_state.clear_inactive(
        {
            "device-unreachable-R8"
        }
    )

    loaded = (
        incident_state.load_state()
    )

    incidents = loaded[
        "incidents"
    ]

    assert (
        "interface-down-cable-24"
        not in incidents
    )

    assert (
        "device-unreachable-R8"
        in incidents
    )


def test_clear_inactive_removes_all_when_network_healthy(
    tmp_path,
    monkeypatch,
):

    state_file = (
        tmp_path
        / "incident-state.json"
    )

    monkeypatch.setattr(
        incident_state,
        "STATE_FILE",
        state_file,
    )

    state = {
        "incidents": {
            "interface-down-cable-24": {
                "last_attempt_at":
                    datetime.now(
                        timezone.utc
                    ).isoformat(),

                "last_status":
                    "ESCALATED",

                "issue_type":
                    "interface_down",

                "source":
                    "R6",

                "target":
                    "R7",
            }
        }
    }

    incident_state.save_state(
        state
    )

    incident_state.clear_inactive(
        set()
    )

    loaded = (
        incident_state.load_state()
    )

    assert (
        loaded["incidents"]
        == {}
    )
