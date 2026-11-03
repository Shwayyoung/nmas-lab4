#!/usr/bin/env python3
"""Day-2 orchestration for existing R1/R2/R4/R5 devices."""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DAY2 = ROOT / "automation" / "day2"

ZTP_DEVICES = ("R6", "R7", "R8", "S5")
DAY2_ORDER = ("R2", "R4", "R1", "R5")

ZTP = ROOT / "automation" / "ztp"
sys.path.insert(0, str(ZTP))

from provision import preflight
from state import is_configured

def check_ztp():
    print("[Orchestrator] Checking ZTP state...", flush=True)
    ready = True

    for device in ZTP_DEVICES:
        info = preflight(device)
        configured = (
            info["ready_for_deployment"]
            and is_configured(
                device,
                info["container_id"],
                info["sha256"],
            )
        )

        status = "CONFIGURED" if configured else "NOT READY"
        print(f"[Orchestrator] {device}: {status}", flush=True)

        if not configured:
            ready = False

    return ready

def run_device(name, apply=False):
    command = [
        sys.executable,
        str(DAY2 / f"configure_{name.lower()}.py"),
    ]
    if apply:
        command.append("--apply")

    print(f"[Orchestrator] Starting {name}...", flush=True)
    result = subprocess.run(command, check=False)

    if result.returncode != 0:
        raise RuntimeError(
            f"{name} Day-2 failed: exit={result.returncode}"
        )

    print(f"[Orchestrator] {name}: SUCCESS", flush=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if not check_ztp():
        raise RuntimeError("ZTP incomplete. Day-2 provisioning blocked.")

    print("[Orchestrator] ZTP complete.", flush=True)

    # Dependency-aware order:
    # R2 establishes R8 path; R4 establishes R6 path and exports;
    # R1 propagates new routes to legacy RIP/IS-IS;
    # R5 accepts the new host prefixes from BGP.
    for device in DAY2_ORDER:
        run_device(device, apply=args.apply)

    print(
        "[Orchestrator] DAY-2 COMPLETED"
        if args.apply
        else "[Orchestrator] DRY RUN COMPLETED",
        flush=True,
    )

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"[Orchestrator] ERROR: {error}", file=sys.stderr)
        sys.exit(1)
