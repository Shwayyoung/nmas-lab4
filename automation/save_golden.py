#!/usr/bin/env python3
'''
for d in R1 R2 R3 R4 R5 S1 S2 S3 S4; do
    python3 automation/save_golden.py "$d"
done
'''

import sys
import shutil
import subprocess
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parent.parent

AUTOMATION_DIR = BASE / "automation"
RENDER_SCRIPT = AUTOMATION_DIR / "render_config.py"

GENERATED_DIR = BASE / "configs" / "generated"
GOLDEN_DIR = BASE / "golden"
INVENTORY_DIR = BASE / "inventory" / "host_vars"


def fail(message, exit_code=1):
    print(f"ERROR: {message}")
    sys.exit(exit_code)


def render_config(device):
    """
    Run render_config.py for the requested device.
    """
    print(f"[1/3] Rendering configuration for {device}...")

    result = subprocess.run(
        [sys.executable, str(RENDER_SCRIPT), device],
        cwd=BASE,
        text=True,
        capture_output=True,
    )

    if result.stdout:
        print(result.stdout.strip())

    if result.returncode != 0:
        if result.stderr:
            print(result.stderr.strip())
        fail(f"Configuration rendering failed for {device}.")

    generated_file = GENERATED_DIR / f"{device}.cfg"

    if not generated_file.exists():
        fail(
            f"render_config.py completed, but generated configuration "
            f"was not found: {generated_file}"
        )

    return generated_file


def save_golden(device, generated_file):
    """
    Save timestamped and latest copies of the generated configuration.
    """
    print(f"[2/3] Saving Golden Configuration for {device}...")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    device_dir = GOLDEN_DIR / device
    device_dir.mkdir(parents=True, exist_ok=True)

    timestamped_file = device_dir / f"{device}-{timestamp}.cfg"
    latest_file = device_dir / f"{device}-latest.cfg"

    shutil.copy2(generated_file, timestamped_file)
    shutil.copy2(generated_file, latest_file)

    return timestamped_file, latest_file


def main():
    if len(sys.argv) != 2:
        print("Usage:")
        print("  python3 automation/save_golden.py <device>")
        print()
        print("Example:")
        print("  python3 automation/save_golden.py R3")
        sys.exit(1)

    device = sys.argv[1].strip()

    yaml_file = INVENTORY_DIR / f"{device}.yml"

    if not yaml_file.exists():
        fail(f"Inventory YAML not found: {yaml_file}")

    if not RENDER_SCRIPT.exists():
        fail(f"Render script not found: {RENDER_SCRIPT}")

    generated_file = render_config(device)

    timestamped_file, latest_file = save_golden(
        device,
        generated_file,
    )

    print("[3/3] Golden Configuration completed successfully.")
    print()
    print(f"Device:    {device}")
    print(f"Generated: {generated_file}")
    print(f"Golden:    {timestamped_file}")
    print(f"Latest:    {latest_file}")


if __name__ == "__main__":
    main()