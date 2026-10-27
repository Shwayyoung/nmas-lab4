#!/usr/bin/env python3
"""Verify critical EOS configuration lines. Read-only."""

import argparse
import subprocess
from pathlib import Path

from provision import preflight

PROJECT = Path(__file__).resolve().parents[2]


def sections(config):
    result = {}
    current = None

    for raw in config.splitlines():
        line = raw.strip()

        if not line or line == "!" or line == "end":
            current = None
            continue

        if raw.startswith("interface "):
            current = line
            result[current] = set()
        elif raw.startswith(("router ", "ipv6 router ")):
            current = line
            result[current] = set()
        elif current and raw.startswith((" ", "\t")):
            result[current].add(line)

    return result


def verify(device, running):
    expected_path = PROJECT / "configs/generated" / f"{device}.cfg"
    expected = expected_path.read_text()

    actual_sections = sections(running)
    expected_sections = sections(expected)
    missing = []

    if f"hostname {device}" not in running.splitlines():
        missing.append(f"hostname {device}")

    # Verify critical interface configuration.
    critical = (
        "ip address ",
        "ipv6 address ",
        "no switchport",
        "switchport mode ",
        "switchport access vlan ",
        "ip ospf area ",
        "ipv6 ospf ",
        "isis enable ",
        "isis passive",
        "isis network ",
    )

    for name, commands in expected_sections.items():
        if not name.startswith("interface "):
            continue

        if name not in actual_sections:
            missing.append(name)
            continue

        for command in commands:
            if not command.startswith(critical):
                continue

            # EOS use Access Mode by default
            # show running-config may not have switchport mode access。
            if command == "switchport mode access":
                # If configured as Trunk Mode，it will failed.
                if "switchport mode trunk" in actual_sections[name]:
                    missing.append(
                        f"{name}: expected access mode, found trunk mode"
                    )
                continue

            if command not in actual_sections[name]:
                missing.append(f"{name}: {command}")

    # Verify key routing processes exist.
    for name in expected_sections:
        if name.startswith(("router ", "ipv6 router ")):
            if name not in actual_sections:
                missing.append(name)

    # Verify VLAN existence for Layer 2 devices.
    for line in expected.splitlines():
        if line.startswith("vlan "):
            if line not in running.splitlines():
                missing.append(line)

    return missing


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("device", choices=["R6", "R7", "R8", "S5"])
    parser.add_argument("--running-file")
    args = parser.parse_args()

    if args.running_file:
        running = Path(args.running_file).read_text()
    else:
        info = preflight(args.device)
        if not info["ready_for_deployment"]:
            raise RuntimeError("Container is not running")

        result = subprocess.run(
            ["docker", "exec", info["container"],
             "Cli", "-p", "15", "-c", "show running-config"],
            capture_output=True, text=True,
            check=True, timeout=60
        )
        running = result.stdout

    missing = verify(args.device, running)

    if missing:
        print(f"[ZTP] {args.device}: verification FAILED")
        for item in missing:
            print(f"  MISSING: {item}")
        raise SystemExit(1)

    print(f"[ZTP] {args.device}: critical configuration verified")


if __name__ == "__main__":
    main()
