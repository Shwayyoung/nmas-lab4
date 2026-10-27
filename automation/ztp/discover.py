#!/usr/bin/env python3

import json
import subprocess

LAB = "5840-lab1"
ZTP_DEVICES = {"R6", "R7", "R8", "S5"}


def docker_ps():
    cmd = [
        "docker", "ps", "-a",
        "--filter", f"label=containerlab={LAB}",
        "--format", "{{json .}}"
    ]

    result = subprocess.run(
        cmd, capture_output=True, text=True, check=True
    )

    return [
        json.loads(line)
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def discover():
    containers = docker_ps()

    print(f"{'DEVICE':<10} {'STATUS':<20} {'ZTP':<12}")
    print("-" * 45)

    for container in sorted(containers, key=lambda x: x["Names"]):
        name = container["Names"]

        prefix = f"clab-{LAB}-"
        if not name.startswith(prefix):
            continue

        device = name[len(prefix):]
        status = container["Status"]

        if device in ZTP_DEVICES:
            ztp = "PENDING"
        else:
            ztp = "EXISTING"

        print(f"{device:<10} {status:<20} {ztp:<12}")


if __name__ == "__main__":
    discover()
