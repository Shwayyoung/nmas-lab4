#!/usr/bin/env python3
"""ZTP provisioning with explicit checkpoint and rollback."""

import argparse
import json
import subprocess
import sys

from bootstrap import wait_until_ready
from provision import preflight
from stage_config import stage
from state import set_state, set_configured, is_configured
from verify_config import verify as verify_running

DEVICES = {"R6", "R7", "R8", "S5"}
DEST = "flash:ztp-candidate.cfg"


def cli(container, command, timeout=60):
    result = subprocess.run(
        ["docker", "exec", container, "Cli", "-p", "15", "-c", command],
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    output = result.stdout + result.stderr

    error_markers = (
        "% Invalid",
        "% Error",
        "% Incomplete",
        "% Ambiguous",
        "Error:",
        "Invalid command",
    )

    if result.returncode != 0 or any(
        marker.lower() in output.lower()
        for marker in error_markers
    ):
        raise RuntimeError(
            f"EOS command failed: {command}\n{output}"
        )

    return output


def normalized(config):
    """Remove non-semantic separators for rollback comparison."""
    return [
        line.rstrip()
        for line in config.splitlines()
        if line.strip()
        and not line.lstrip().startswith("!")
        and line.strip() != "end"
    ]


def verify_device(device, container):
    running = cli(container, "show running-config")
    missing = verify_running(device, running)

    if missing:
        raise RuntimeError(
            "Configuration verification failed:\n"
            + "\n".join(missing)
        )

    return running


def rollback(container, checkpoint, before):
    print(f"[ZTP] Restoring checkpoint: {checkpoint}", flush=True)

    cli(
        container,
        f"configure checkpoint restore {checkpoint}",
        timeout=120,
    )

    restored = cli(container, "show running-config")

    if normalized(restored) != normalized(before):
        raise RuntimeError(
            "Checkpoint restore returned, but running-config "
            "does not exactly match the pre-change snapshot"
        )

    print("[ZTP] Rollback verified", flush=True)


def apply(device):
    if device not in DEVICES:
        raise ValueError("Unauthorized ZTP device")

    info = preflight(device)

    if not info["ready_for_deployment"]:
        raise RuntimeError(f"{device}: container not running")

    container = info["container"]

    if not wait_until_ready(device, retries=30, interval=10):
        raise RuntimeError(f"{device}: EOS CLI not ready")

    if is_configured(
        device, info["container_id"], info["sha256"]
    ):
        try:
            verify_device(device, container)
        except Exception:
            print(f"[ZTP] {device}: drift detected; reprovisioning")
        else:
            print(f"[ZTP] {device}: already configured")
            return

    checkpoint = f"ztp_pre_{device}_{info['container_id'][:8]}"
    checkpoint_saved = False
    replace_attempted = False
    before = None

    set_state(device, "PROVISIONING", "Starting provisioning")

    try:
        # Stage candidate and verify SHA256.
        stage(device)

        # Capture current running configuration.
        before = cli(container, "show running-config")

        # Explicitly save a known rollback point.
        cli(
            container,
            f"configure checkpoint save {checkpoint}",
            timeout=60,
        )
        checkpoint_saved = True

        print(f"[ZTP] Checkpoint saved: {checkpoint}")

        # Apply the complete configuration.
        replace_attempted = True
        cli(
            container,
            f"configure replace {DEST}",
            timeout=120,
        )

        # Verify before saving startup configuration.
        verify_device(device, container)

        cli(
            container,
            "copy running-config startup-config",
            timeout=60,
        )

        set_configured(
            device,
            info["container_id"],
            info["sha256"],
        )

        print(f"[ZTP] {device}: CONFIGURED")

    except Exception as original_error:
        detail = str(original_error)

        if checkpoint_saved and replace_attempted:
            try:
                rollback(container, checkpoint, before)
                detail += " | Rollback verified"
            except Exception as rollback_error:
                detail += f" | ROLLBACK FAILED: {rollback_error}"

        set_state(device, "FAILED", detail)
        raise RuntimeError(detail) from original_error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("device", choices=sorted(DEVICES))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if not args.apply:
        print(json.dumps(preflight(args.device), indent=2))
        print("[ZTP] DRY RUN - no changes applied")
        return

    apply(args.device)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ZTP] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
