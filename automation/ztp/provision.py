#!/usr/bin/env python3
"""ZTP provisioning preflight. No configuration is pushed."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
LAB = "5840-lab1"
DEVICES = {"R6", "R7", "R8", "S5"}


def run(command):
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout


def preflight(device):
    if device not in DEVICES:
        raise ValueError(f"Device not authorized for ZTP: {device}")

    inventory = PROJECT / "inventory/host_vars" / f"{device}.yml"
    config = PROJECT / "configs/generated" / f"{device}.cfg"
    container = f"clab-{LAB}-{device}"

    if not inventory.is_file():
        raise FileNotFoundError(inventory)

    if not config.is_file():
        raise FileNotFoundError(config)

    content = config.read_text()

    if not content.strip():
        raise ValueError("Empty configuration")

    if f"hostname {device}" not in content.splitlines():
        raise ValueError("Hostname mismatch")

    for marker in ("interface Management0", "management api http-commands"):
        if marker not in content:
            raise ValueError(f"Missing configuration: {marker}")

    inspect = subprocess.run(
        ["docker", "inspect", container],
        capture_output=True,
        text=True,
        timeout=15,
    )

    if inspect.returncode == 0:
        info = json.loads(inspect.stdout)[0]
        labels = info["Config"].get("Labels") or {}
        if labels.get("containerlab") != LAB:
            raise ValueError("ContainerLab label mismatch")
        container_status = info["State"]["Status"]
        container_id = info["Id"][:12]
    else:
        container_status = "NOT_DEPLOYED"
        container_id = None

    return {
        "device": device,
        "container": container,
        "container_id": container_id,
        "status": container_status,
        "config": str(config),
        "lines": len(content.splitlines()),
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
        "ready_for_deployment": container_status == "running",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("device", choices=sorted(DEVICES))
    args = parser.parse_args()

    result = preflight(args.device)
    print(json.dumps(result, indent=2))

    print("\n[ZTP] DRY RUN ONLY - no changes applied")


if __name__ == "__main__":
    main()
