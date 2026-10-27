#!/usr/bin/env python3

import subprocess
import time

LAB = "5840-lab1"
ZTP_DEVICES = {"R6", "R7", "R8", "S5"}


def run_cli(device, command, timeout=15):
    """Execute a read-only EOS CLI command."""
    if device not in ZTP_DEVICES:
        raise ValueError(f"Unauthorized ZTP device: {device}")

    container = f"clab-{LAB}-{device}"

    return subprocess.run(
        ["docker", "exec", container, "Cli", "-p", "15", "-c", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True
    ).stdout


def wait_until_ready(device, retries=30, interval=10):
    """Wait until EOS CLI responds successfully."""
    for attempt in range(1, retries + 1):
        try:
            output = run_cli(device, "show version")

            if "Arista cEOSLab" in output:
                print(f"[ZTP] {device}: EOS ready")
                return True

        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
        ):
            pass

        print(
            f"[ZTP] {device}: waiting for EOS "
            f"({attempt}/{retries})",
            flush=True
        )
        time.sleep(interval)

    print(f"[ZTP] {device}: EOS readiness timeout")
    return False


if __name__ == "__main__":
    for device in sorted(ZTP_DEVICES):
        try:
            wait_until_ready(device, retries=1, interval=1)
        except Exception as exc:
            print(f"[ZTP] {device}: {exc}")
