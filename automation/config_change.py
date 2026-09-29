#!/usr/bin/env python3

import argparse
import difflib
import subprocess
import sys
import json

from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = Path(
    "/home/student/labs/nmas-lab4"
)

RENDER_SCRIPT = (
    BASE_DIR
    / "automation"
    / "render_config.py"
)

HOST_VARS_DIR = (
    BASE_DIR
    / "inventory"
    / "host_vars"
)

GENERATED_DIR = (
    BASE_DIR
    / "configs"
    / "generated"
)

GOLDEN_DIR = (
    BASE_DIR
    / "golden"
)

CHANGE_HISTORY_DIR = (
    BASE_DIR
    / "reports"
    / "config_changes"
)

# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

def get_device_names():

    devices = []

    for path in HOST_VARS_DIR.glob("*.yml"):

        devices.append(
            path.stem
        )

    for path in HOST_VARS_DIR.glob("*.yaml"):

        if path.stem not in devices:
            devices.append(
                path.stem
            )

    return sorted(devices)


# ---------------------------------------------------------------------------
# Render desired configuration
# ---------------------------------------------------------------------------

def render_device(device):

    print(
        f"Rendering desired config "
        f"for {device}..."
    )

    result = subprocess.run(
        [
            sys.executable,
            str(RENDER_SCRIPT),
            device,
        ],
        cwd=BASE_DIR,
        text=True,
        capture_output=True,
    )

    if result.returncode != 0:

        raise RuntimeError(
            f"Render failed for "
            f"{device}:\n"
            f"{result.stderr}"
        )

    generated = (
        GENERATED_DIR
        / f"{device}.cfg"
    )

    if not generated.exists():

        raise RuntimeError(
            f"Generated config not found: "
            f"{generated}"
        )

    return generated


# ---------------------------------------------------------------------------
# Golden config
# ---------------------------------------------------------------------------

def get_golden_config(device):

    path = (
        GOLDEN_DIR
        / device
        / f"{device}-latest.cfg"
    )

    if not path.exists():

        raise RuntimeError(
            f"Golden config not found: "
            f"{path}"
        )

    return path


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def compare_configs(
    device,
):

    generated = render_device(
        device
    )

    golden = get_golden_config(
        device
    )

    old_lines = golden.read_text(
        encoding="utf-8"
    ).splitlines(
        keepends=True
    )

    new_lines = generated.read_text(
        encoding="utf-8"
    ).splitlines(
        keepends=True
    )

    diff = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=(
                f"{device}-golden"
            ),
            tofile=(
                f"{device}-desired"
            ),
        )
    )

    return {
        "device": device,
        "generated": generated,
        "golden": golden,
        "changed": bool(diff),
        "diff": "".join(diff),
    }


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def show_result(result):

    device = result["device"]

    print(
        f"\n===== {device} ====="
    )

    if not result["changed"]:

        print(
            "NO CONFIGURATION CHANGES"
        )

        return

    print(
        "CONFIGURATION CHANGE DETECTED"
    )

    print()

    print(
        result["diff"]
    )

def save_change_report(result):

    CHANGE_HISTORY_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%d-%H%M%S")

    device = result["device"]

    report = {
        "device": device,
        "timestamp": timestamp,
        "changed": result["changed"],
        "golden": str(result["golden"]),
        "generated": str(result["generated"]),
        "diff": result["diff"],
    }

    path = (
        CHANGE_HISTORY_DIR
        / f"{device}-{timestamp}.json"
    )

    path.write_text(
        json.dumps(
            report,
            indent=2,
        ),
        encoding="utf-8",
    )

    latest = (
        CHANGE_HISTORY_DIR
        / f"{device}-latest.json"
    )

    latest.write_text(
        json.dumps(
            report,
            indent=2,
        ),
        encoding="utf-8",
    )

    return path

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():

    parser = argparse.ArgumentParser(
        description=(
            "NMAS configuration "
            "change preview"
        )
    )

    parser.add_argument(
        "device",
        nargs="?",
        help="Device name",
    )

    parser.add_argument(
        "--all",
        action="store_true",
        help=(
            "Compare all inventory "
            "devices"
        ),
    )

    args = parser.parse_args()

    if args.all:

        devices = get_device_names()

    else:

        if not args.device:

            parser.error(
                "Specify DEVICE "
                "or use --all"
            )

        devices = [
            args.device
        ]

    changed = 0
    unchanged = 0
    failed = 0

    for device in devices:

        try:

            result = compare_configs(
                device
            )

            show_result(
                result
            )

            save_change_report(
                result
            )

            if result["changed"]:
                changed += 1

            else:
                unchanged += 1

        except Exception as exc:

            failed += 1

            print(
                f"\n===== {device} ====="
            )

            print(
                f"ERROR: {exc}",
                file=sys.stderr,
            )

    print(
        "\n===== Summary ====="
    )

    print(
        f"Changed:   {changed}"
    )

    print(
        f"Unchanged: {unchanged}"
    )

    print(
        f"Failed:    {failed}"
    )


if __name__ == "__main__":

    try:

        main()

    except Exception as exc:

        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )

        sys.exit(1)
