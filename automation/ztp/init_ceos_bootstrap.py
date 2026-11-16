#!/usr/bin/env python3

import re
import subprocess
import sys
from pathlib import Path

SECRETS_FILE = Path("/etc/nmas-secrets")

CEOS_DEVICES = {
    "R6": "clab-5840-lab1-R6",
    "R7": "clab-5840-lab1-R7",
    "R8": "clab-5840-lab1-R8",
    "S5": "clab-5840-lab1-S5",
}


def read_secret(name):
    text = SECRETS_FILE.read_text()

    m = re.search(
        rf"^{re.escape(name)}=(?:'([^']*)'|\"([^\"]*)\"|(.*))$",
        text,
        re.MULTILINE,
    )

    if not m:
        raise RuntimeError(f"{name} not found in {SECRETS_FILE}")

    return next(v for v in m.groups() if v is not None).strip()


def run_cli(container, commands):
    proc = subprocess.run(
        ["docker", "exec", "-i", container, "Cli", "-p", "15"],
        input="\n".join(commands) + "\n",
        text=True,
        capture_output=True,
    )

    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())

    return proc.stdout


def main():
    if not SECRETS_FILE.exists():
        print(f"ERROR: {SECRETS_FILE} not found", file=sys.stderr)
        return 1

    password = read_secret("ARISTA_BOOTSTRAP_PASSWORD")

    failures = []

    for device, container in CEOS_DEVICES.items():
        print(f"\n=== {device} ===")

        try:
            check = run_cli(
                container,
                ["show running-config all | include ^username admin"],
            )

            if "nopassword" not in check:
                print("admin already has a password; skipping.")
                continue

            print("admin is nopassword; installing bootstrap credential...")

            run_cli(
                container,
                [
                    "enable",
                    "configure terminal",
                    f"username admin privilege 15 role network-admin secret 0 {password}",
                    "end",
                    "write memory",
                ],
            )

            verify = run_cli(
                container,
                ["show running-config all | include ^username admin"],
            )

            if "nopassword" in verify:
                raise RuntimeError("admin still shows nopassword after configuration")

            print("Bootstrap credential installed successfully.")

        except Exception as exc:
            print(f"{device}: FAILED: {exc}")
            failures.append(device)

    print("\n=== Summary ===")

    if failures:
        print("Failed:", ", ".join(failures))
        return 1

    print("All cEOS bootstrap credentials are ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
