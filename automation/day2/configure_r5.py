#!/usr/bin/env python3
"""R5 Day-2 provisioning: permit VLAN40/50 in BGP host import policy."""

import argparse
import datetime
import subprocess
import sys
import time
import re
from pathlib import Path

R5 = "clab-5840-lab1-R5"
ROOT = Path(__file__).resolve().parents[2]
BACKUP = ROOT / "configs" / "day2-backups"
CONFIRM_TIMEOUT = 300

COMMANDS = [
    "set /routing-policy prefix-set HOST-IPV4 prefix 192.168.40.0/24 mask-length-range 24..24",
    "set /routing-policy prefix-set HOST-IPV4 prefix 192.168.50.0/24 mask-length-range 24..24",
    "set /routing-policy prefix-set HOST-IPV6 prefix 2001:db8:5840:40::/64 mask-length-range 64..64",
    "set /routing-policy prefix-set HOST-IPV6 prefix 2001:db8:5840:50::/64 mask-length-range 64..64",
]

PREFIXES_V4 = ("192.168.40.0/24", "192.168.50.0/24")
PREFIXES_V6 = ("2001:db8:5840:40::/64", "2001:db8:5840:50::/64")

def run(cmd, input_text=None, timeout=60):
    result = subprocess.run(
        cmd, input=input_text, capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {cmd}\nexit={result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    return result.stdout + result.stderr

def read(path):
    return run(["docker", "exec", R5, "sr_cli", "-d", f"info {path}"])

def show(command):
    return run(["docker", "exec", R5, "sr_cli", "-d", command])

def cli(command):
    return run(["docker", "exec", R5, "sr_cli", "-e", "-d", command])

def batch(commands):
    script = "\n".join(commands) + "\n"
    return run(
        [
            "docker", "exec", "-i", R5,
            "sr_cli", "--candidate-mode", "--dumb-terminal", "--echo",
        ],
        input_text=script,
        timeout=120,
    )

def snapshot():
    BACKUP.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = BACKUP / f"R5-{stamp}.txt"
    policy = read("/routing-policy")
    target.write_text(policy)
    print(f"[R5] Snapshot saved: {target}")
    return policy

def verify_policy():
    policy = read("/routing-policy")
    for prefix in PREFIXES_V4 + PREFIXES_V6:
        if prefix not in policy:
            raise RuntimeError(f"Missing R5 BGP import prefix: {prefix}")
    print("[R5] BGP import prefix sets verified")

def verify_routes(timeout=120):
    deadline = time.monotonic() + timeout

    while True:
        ipv4 = show(
            "show network-instance default ipv4 route all"
        )

        ipv6 = show(
            "show network-instance default ipv6 route all"
        )

        missing = []

        # IPv4 output keeps prefix and mask together.
        for prefix in PREFIXES_V4:
            if prefix not in ipv4:
                missing.append(prefix)

        # SR Linux may print an IPv6 route like:
        #
        # 2001:db8:5840:40::   bgp ...
        # ...
        # /64
        #
        # Therefore match the address portion rather than expecting
        # "2001:db8:5840:40::/64" to be contiguous.
        for prefix in PREFIXES_V6:
            address = prefix.split("/")[0]

            if not re.search(
                rf"{re.escape(address)}\s+bgp\b",
                ipv6,
                re.IGNORECASE,
            ):
                missing.append(prefix)

        if not missing:
            print(
                "[R5] VLAN40/50 IPv4/IPv6 routes installed"
            )
            return

        if time.monotonic() >= deadline:
            raise RuntimeError(
                "R5 missing learned routes: "
                + ", ".join(missing)
            )

        time.sleep(5)


def verify_rollback(before):
    current = read("/routing-policy")
    if current != before:
        raise RuntimeError("R5 routing-policy does not match pre-change snapshot")
    print("[R5] Rollback verified")

def apply():
    try:
        verify_policy()
        verify_routes(timeout=20)
        print("[R5] Already configured; no changes necessary")
        return
    except RuntimeError:
        print("[R5] Day-2 configuration required")

    before = snapshot()
    commit_attempted = False
    accepted = False

    try:
        commit_attempted = True
        batch(COMMANDS + [
            "commit validate",
            "diff",
            f"commit confirmed timeout {CONFIRM_TIMEOUT}",
        ])
        print("[R5] Confirmed commit submitted")

        verify_policy()
        verify_routes()

        cli("commit confirmed accept")
        accepted = True
        print("[R5] Confirmed commit ACCEPTED")
        print("[R5] Day-2 provisioning completed")

    except Exception as error:
        detail = str(error)
        if commit_attempted and not accepted:
            try:
                print("[R5] Rejecting confirmed commit")
                cli("commit confirmed reject")
                verify_rollback(before)
            except Exception as rollback_error:
                detail += (
                    "\nROLLBACK NOT VERIFIED: "
                    + str(rollback_error)
                    + "\nCheck R5 confirmed-commit state."
                )
        raise RuntimeError(detail) from error

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if args.apply:
        apply()
    else:
        print("[R5] DRY RUN")
        verify_policy()
        verify_routes(timeout=10)

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"[R5] ERROR: {error}", file=sys.stderr)
        sys.exit(1)
