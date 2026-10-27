#!/usr/bin/env python3
"""Stage a ZTP configuration without applying it."""

import argparse
import hashlib
import subprocess

from provision import preflight

DEST = "/mnt/flash/ztp-candidate.cfg"


def run(cmd):
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        check=True, timeout=30
    )
    return result.stdout.strip()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def stage(device):
    info = preflight(device)

    if not info["ready_for_deployment"]:
        raise RuntimeError(
            f"{device}: container is not running"
        )

    container = info["container"]
    config = info["config"]

    # Copy generated configuration into cEOS.
    run(["docker", "cp", config, f"{container}:{DEST}"])

    # Read file back through Docker and verify bytes.
    result = subprocess.run(
        ["docker", "exec", container, "cat", DEST],
        capture_output=True, check=True, timeout=30
    )

    actual_hash = sha256(result.stdout)

    if actual_hash != info["sha256"]:
        raise RuntimeError(
            f"{device}: configuration checksum mismatch"
        )

    print(f"[ZTP] {device}: configuration staged")
    print(f"[ZTP] SHA256: {actual_hash}")
    print("[ZTP] Configuration NOT applied")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "device",
        choices=["R6", "R7", "R8", "S5"]
    )
    args = parser.parse_args()
    stage(args.device)
