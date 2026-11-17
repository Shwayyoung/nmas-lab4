#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path


SELF_HEALING_DIR = Path(__file__).resolve().parent
AUTOMATION_DIR = SELF_HEALING_DIR.parent
PROJECT_ROOT = AUTOMATION_DIR.parent

sys.path.insert(
    0,
    str(SELF_HEALING_DIR),
)

sys.path.insert(
    0,
    str(AUTOMATION_DIR),
)


from detector import (
    detect_interface_faults,
    detect_unreachable_devices,
    get_topology,
)

from diagnosis import (
    diagnose_interface_incident,
)

from incident_store import (
    save_incidents,
)

from remediation import (
    remediate_interface_shutdown,
)

from verifier import (
    verify_incident_cleared,
)

from interface_control import (
    connect,
    get_device,
)

from incident_state import (
    clear_inactive,
    get_incident_state,
    record_processed,
    should_process,
)

# ---------------------------------------------------------
# Management-plane SSH validation
# ---------------------------------------------------------

def verify_ssh_reachability(
    device_name,
):

    device = get_device(
        device_name
    )

    print(
        f"[Self-Healing] "
        f"Attempting SSH connection to "
        f"{device_name} "
        f"({device['mgmt_ip']})",
        flush=True,
    )

    client = connect(
        device
    )

    try:

        transport = (
            client.get_transport()
        )

        if (
            transport is None
            or not transport.is_active()
        ):

            raise RuntimeError(
                "SSH transport is not active."
            )

        print(
            f"[Self-Healing] "
            f"SSH connection to "
            f"{device_name} succeeded.",
            flush=True,
        )

        return True

    finally:

        client.close()


# ---------------------------------------------------------
# Device unreachable handling
# ---------------------------------------------------------

def process_device_unreachable(
    incident,
):

    print(
        f"[Self-Healing] "
        f"Processing unreachable device: "
        f"{incident.source}",
        flush=True,
    )

    incident.troubleshooting_steps.append(
        "ICMP management-plane reachability "
        "check failed."
    )

    # -----------------------------------------------------
    # Attempt real SSH connection to management IP.
    # -----------------------------------------------------

    try:

        ssh_ok = (
            verify_ssh_reachability(
                incident.source
            )
        )

    except Exception as exc:

        incident.troubleshooting_steps.append(
            f"Management-plane SSH connection "
            f"failed: {exc}"
        )

        incident.remediation_actions.append(
            "Automatic configuration recovery "
            "could not be performed because the "
            "device was unreachable through SSH."
        )

        incident.remediation_actions.append(
            "Automatic device/container restart "
            "was not performed because power or "
            "container recovery is outside the "
            "approved self-healing scope."
        )

        incident.verification.append(
            "Management-plane SSH connectivity "
            "remained unavailable."
        )

        incident.troubleshooting_steps.extend(
            [
                (
                    "Verify that the device is "
                    "powered on and the network OS "
                    "is running."
                ),
                (
                    "Verify management network "
                    "connectivity to the device."
                ),
                (
                    "Verify the management interface "
                    "IP address and gateway."
                ),
                (
                    "Verify that the SSH service is "
                    "running and reachable."
                ),
                (
                    "Check device or container logs "
                    "for startup or service failures."
                ),
            ]
        )

        incident.status = (
            "ESCALATED"
        )

        print(
            f"[Self-Healing] "
            f"{incident.incident_id}: "
            f"ESCALATED - SSH management "
            f"connection failed",
            flush=True,
        )

        return

    # -----------------------------------------------------
    # ICMP failed but SSH succeeded.
    #
    # This means the device is actually manageable, so
    # we should NOT declare the whole device unreachable.
    # ICMP may simply be filtered.
    # -----------------------------------------------------

    if ssh_ok:

        incident.troubleshooting_steps.append(
            "SSH management connectivity succeeded "
            "even though ICMP failed."
        )

        incident.remediation_actions.append(
            "No automatic device recovery was "
            "required because SSH management "
            "connectivity is available."
        )

        incident.verification.append(
            "Management-plane SSH connectivity "
            "verified successfully."
        )

        incident.status = (
            "HEALED"
        )

        print(
            f"[Self-Healing] "
            f"{incident.incident_id}: "
            f"HEALED - SSH reachable; "
            f"ICMP failure treated as non-critical",
            flush=True,
        )


# ---------------------------------------------------------
# Interface-down handling
# ---------------------------------------------------------

def process_interface_incident(
    incident,
):

    print(
        f"[Self-Healing] "
        f"Diagnosing "
        f"{incident.incident_id}",
        flush=True,
    )

    diagnosis = (
        diagnose_interface_incident(
            incident
        )
    )

    if not diagnosis:

        incident.status = (
            "ESCALATED"
        )

        incident.troubleshooting_steps.append(
            "No supported automatic remediation "
            "was identified."
        )

        incident.troubleshooting_steps.append(
            "Verify interface configuration, "
            "physical connectivity and peer state."
        )

        print(
            f"[Self-Healing] "
            f"{incident.incident_id}: "
            f"ESCALATED - no supported remediation",
            flush=True,
        )

        return

    print(
        f"[Self-Healing] "
        f"Cause identified: "
        f"{diagnosis['cause']} "
        f"on "
        f"{diagnosis['device']} "
        f"{diagnosis['interface']}",
        flush=True,
    )

    # -----------------------------------------------------
    # Remediation
    # -----------------------------------------------------

    try:

        remediate_interface_shutdown(
            incident,
            diagnosis,
        )

    except Exception as exc:

        incident.status = (
            "ESCALATED"
        )

        incident.remediation_actions.append(
            f"Automatic remediation failed: "
            f"{exc}"
        )

        incident.troubleshooting_steps.extend(
            [
                (
                    "Verify management-plane SSH "
                    "connectivity to the affected "
                    "device."
                ),
                (
                    "Verify the automation account "
                    "has privileged configuration "
                    "access."
                ),
                (
                    "Verify the interface "
                    "configuration manually."
                ),
            ]
        )

        print(
            f"[Self-Healing] "
            f"{incident.incident_id}: "
            f"ESCALATED - remediation failed: "
            f"{exc}",
            flush=True,
        )

        return

    print(
        "[Self-Healing] "
        "Remediation applied; "
        "waiting for telemetry verification",
        flush=True,
    )

    # -----------------------------------------------------
    # Verification
    # -----------------------------------------------------

    try:

        verified = (
            verify_incident_cleared(
                incident
            )
        )

    except Exception as exc:

        incident.status = (
            "ESCALATED"
        )

        incident.verification.append(
            f"Verification failed: "
            f"{exc}"
        )

        incident.troubleshooting_steps.append(
            "Verify telemetry collection "
            "and monitoring availability."
        )

        print(
            f"[Self-Healing] "
            f"{incident.incident_id}: "
            f"ESCALATED - verification error: "
            f"{exc}",
            flush=True,
        )

        return

    if verified:

        incident.status = (
            "HEALED"
        )

        print(
            f"[Self-Healing] "
            f"{incident.incident_id}: "
            f"HEALED",
            flush=True,
        )

    else:

        incident.status = (
            "ESCALATED"
        )

        incident.verification.append(
            "Automatic remediation did not "
            "restore the expected link state."
        )

        incident.troubleshooting_steps.extend(
            [
                (
                    "Verify physical or virtual "
                    "link connectivity."
                ),
                (
                    "Verify the peer interface "
                    "state."
                ),
                (
                    "Verify VLAN, routing and "
                    "interface configuration on "
                    "both endpoints."
                ),
            ]
        )

        print(
            f"[Self-Healing] "
            f"{incident.incident_id}: "
            f"ESCALATED - verification failed",
            flush=True,
        )


# ---------------------------------------------------------
# Incident dispatcher
# ---------------------------------------------------------

def process_incident(
    incident,
):

    if (
        incident.issue_type
        == "device_unreachable"
    ):

        process_device_unreachable(
            incident
        )

        return

    if (
        incident.issue_type
        == "interface_down"
    ):

        process_interface_incident(
            incident
        )

        return

    incident.status = (
        "ESCALATED"
    )

    incident.troubleshooting_steps.append(
        f"Unsupported incident type: "
        f"{incident.issue_type}"
    )

    print(
        f"[Self-Healing] "
        f"{incident.incident_id}: "
        f"ESCALATED - unsupported "
        f"incident type",
        flush=True,
    )


# ---------------------------------------------------------
# Detection cycle
# ---------------------------------------------------------

def run_once():

    # -----------------------------------------------------
    # 1. Detect whole-device reachability failures.
    # -----------------------------------------------------

    try:

        device_incidents = (
            detect_unreachable_devices()
        )

    except Exception as exc:

        print(
            f"[Self-Healing] "
            f"Device reachability detection "
            f"failed: {exc}",
            flush=True,
        )

        device_incidents = []

    unreachable_devices = {
        incident.source
        for incident
        in device_incidents
    }

    # -----------------------------------------------------
    # 2. Detect interface faults.
    #
    # Suppress interfaces attached to an unreachable
    # device so one device outage does not create many
    # duplicate incidents.
    # -----------------------------------------------------

    try:

        topology = (
            get_topology()
        )

        interface_incidents = (
            detect_interface_faults(
                topology,
                ignore_devices=(
                    unreachable_devices
                ),
            )
        )

    except Exception as exc:

        print(
            f"[Self-Healing] "
            f"Interface fault detection "
            f"failed: {exc}",
            flush=True,
        )

        interface_incidents = []

    # -----------------------------------------------------
    # 3. Combine incidents.
    # -----------------------------------------------------

    incidents = (
        device_incidents
        + interface_incidents
    )
    active_ids = {
        incident.incident_id
        for incident in incidents
    }

    clear_inactive(
        active_ids
    )

    print(
        f"[Self-Healing] "
        f"{len(incidents)} "
        f"actionable incident(s)",
        flush=True,
    )

    # -----------------------------------------------------
    # 4. Process incidents.
    # -----------------------------------------------------
    processed_incidents = []
    for incident in incidents:

        if (
            incident.issue_type
            == "device_unreachable"
        ):

            print(
                f"  "
                f"{incident.incident_id}: "
                f"{incident.source} "
                f"management unreachable",
                flush=True,
            )

        elif (
            incident.issue_type
            == "interface_down"
        ):

            print(
                f"  "
                f"{incident.incident_id}: "
                f"{incident.source} "
                f"{incident.source_interface}"
                f"={incident.source_state} "
                f"<-> "
                f"{incident.target} "
                f"{incident.target_interface}"
                f"={incident.target_state}",
                flush=True,
            )

        if not should_process(
            incident.incident_id
        ):

            previous = (
                get_incident_state(
                    incident.incident_id
                )
                or {}
            )

            incident.status = (
                previous.get(
                    "last_status",
                    "DETECTED",
                )
            )

            incident.verification.append(
                "Duplicate active incident detected. "
                "Automatic action skipped during "
                "the cooldown period."
            )

            print(
                f"[Self-Healing] "
                f"{incident.incident_id}: "
                f"SKIPPED - cooldown active",
                flush=True,
            )

            continue

        process_incident(
            incident
        )
        record_processed(
            incident
        )
        processed_incidents.append(
            incident
        )

    # -----------------------------------------------------
    # 5. Persist documentation.
    # -----------------------------------------------------

    history_file = (
        save_incidents(
            incidents,
            history_incidents=processed_incidents,
        )
    )

    if history_file:

        print(
            f"[Self-Healing] "
            f"Incident record: "
            f"{history_file}",
            flush=True,
        )

    return incidents


# ---------------------------------------------------------
# CLI
# ---------------------------------------------------------

def main():

    parser = argparse.ArgumentParser(
        description=(
            "NMAS network self-healing engine"
        )
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help=(
            "Run one self-healing "
            "detection/remediation cycle"
        ),
    )

    args = (
        parser.parse_args()
    )

    if not args.once:

        parser.error(
            "Currently only --once is supported"
        )

    run_once()


if __name__ == "__main__":
    main()