#!/usr/bin/env python3
"""Read-only test of SR Linux CLI command execution."""

import subprocess

CONTAINER = "clab-5840-lab1-R4"


def run_cli(command):
    result = subprocess.run(
        [
            "docker", "exec", CONTAINER,
            "sr_cli", "-d", command,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    print(f"\nCommand: {command}")
    print(f"Return code: {result.returncode}")
    print(f"STDOUT:\n{result.stdout}")
    print(f"STDERR:\n{result.stderr}")

    return result


def main():
    commands = [
        "info /system name",
        "info /interface ethernet-1/4",
        "info /network-instance default protocols ospf",
    ]

    for command in commands:
        result = run_cli(command)

        if result.returncode != 0:
            raise RuntimeError(
                f"Command failed: {command}"
            )


if __name__ == "__main__":
    main()
