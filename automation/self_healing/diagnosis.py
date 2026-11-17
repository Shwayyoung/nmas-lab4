#!/usr/bin/env python3

import sys
from pathlib import Path


SELF_HEALING_DIR = Path(__file__).resolve().parent
AUTOMATION_DIR = SELF_HEALING_DIR.parent

sys.path.insert(0, str(AUTOMATION_DIR))

from interface_control import (
    connect,
    detect_platform,
    get_device,
)

from health_check import (
    read_channel,
    run_shell_command,
)


def arista_is_shutdown(device_name, interface):

    device = get_device(device_name)

    platform = detect_platform(device)

    if platform != "arista":
        return False

    print(
        f"[Self-Healing] SSH connecting to "
        f"{device_name} ({device['mgmt_ip']})",
        flush=True,
    )

    client = connect(device)

    try:
        channel = client.invoke_shell()

        read_channel(channel)

        enable_output = run_shell_command(
            channel,
            "enable",
            timeout=5,
        )

        if "#" not in enable_output.rstrip():
            raise RuntimeError(
                "Failed to enter privileged EXEC mode."
            )

        run_shell_command(
            channel,
            "terminal length 0",
            timeout=5,
        )

        output = run_shell_command(
            channel,
            (
                f"show running-config "
                f"interfaces {interface}"
            ),
            timeout=10,
        )

        # Useful for troubleshooting/demo.
        print(
            f"[Self-Healing] SSH CLI output for "
            f"{device_name} {interface}:",
            flush=True,
        )

        for line in output.splitlines():
            print(
                f"    {line}",
                flush=True,
            )

        lines = {
            line.strip().lower()
            for line in output.splitlines()
        }

        return "shutdown" in lines

    finally:
        client.close()


def diagnose_interface_incident(incident):

    candidates = []

    if incident.source_state == "down":
        candidates.append(
            (
                incident.source,
                incident.source_interface,
            )
        )

    if incident.target_state == "down":
        candidates.append(
            (
                incident.target,
                incident.target_interface,
            )
        )

    for device, interface in candidates:

        message = (
            f"Checking {device} {interface} "
            f"through management-plane SSH."
        )

        print(
            f"[Self-Healing] {message}",
            flush=True,
        )

        incident.troubleshooting_steps.append(
            message
        )

        try:
            shutdown = arista_is_shutdown(
                device,
                interface,
            )

        except Exception as exc:

            message = (
                f"SSH diagnosis failed for "
                f"{device} {interface}: {exc}"
            )

            print(
                f"[Self-Healing] {message}",
                flush=True,
            )

            incident.troubleshooting_steps.append(
                message
            )

            continue

        if shutdown:

            message = (
                f"{device} {interface} is "
                f"administratively shutdown."
            )

            print(
                f"[Self-Healing] {message}",
                flush=True,
            )

            incident.troubleshooting_steps.append(
                message
            )

            return {
                "device": device,
                "interface": interface,
                "cause":
                    "administrative_shutdown",
            }

        message = (
            f"{device} {interface} is operationally "
            f"down but not administratively shutdown."
        )

        print(
            f"[Self-Healing] {message}",
            flush=True,
        )

        incident.troubleshooting_steps.append(
            message
        )

    return None