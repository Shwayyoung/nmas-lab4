#!/usr/bin/env python3
"""R4 Day-2 provisioning: R6 uplink plus BGP export policy updates."""

import argparse
import datetime
import subprocess
import sys
import time
from pathlib import Path

R4 = "clab-5840-lab1-R4"
R6 = "clab-5840-lab1-R6"

ROOT = Path(__file__).resolve().parents[2]
BACKUP = ROOT / "configs" / "day2-backups"
CONFIRM_TIMEOUT = 300

COMMANDS = [
    "set /interface ethernet-1/4 admin-state enable",
    "set /interface ethernet-1/4 subinterface 0 admin-state enable",
    "set /interface ethernet-1/4 subinterface 0 ipv4 admin-state enable",
    "set /interface ethernet-1/4 subinterface 0 ipv4 address 10.0.46.1/30",
    "set /interface ethernet-1/4 subinterface 0 ipv6 admin-state enable",
    "set /interface ethernet-1/4 subinterface 0 ipv6 address 2001:db8:5840:46::4/64",
    "set /network-instance default interface ethernet-1/4.0",
    "set /network-instance default protocols ospf instance ospfv2 area 0.0.0.10 interface ethernet-1/4.0",
    "set /network-instance default protocols ospf instance ospfv3 area 0.0.0.10 interface ethernet-1/4.0",
    "set /routing-policy prefix-set HOST-IPV4 prefix 192.168.40.0/24 mask-length-range 24..24",
    "set /routing-policy prefix-set HOST-IPV4 prefix 192.168.50.0/24 mask-length-range 24..24",
    "set /routing-policy prefix-set HOST-IPV6 prefix 2001:db8:5840:40::/64 mask-length-range 64..64",
    "set /routing-policy prefix-set HOST-IPV6 prefix 2001:db8:5840:50::/64 mask-length-range 64..64",
]

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

def read_r4(path):
    return run(["docker", "exec", R4, "sr_cli", "-d", f"info {path}"])

def cli(command):
    return run(["docker", "exec", R4, "sr_cli", "-e", "-d", command])

def batch(commands):
    script = "\n".join(commands) + "\n"
    return run(
        [
            "docker", "exec", "-i", R4,
            "sr_cli", "--candidate-mode", "--dumb-terminal", "--echo",
        ],
        input_text=script,
        timeout=120,
    )

def snapshot():
    BACKUP.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = BACKUP / f"R4-{stamp}.txt"
    sections = [
        "/interface ethernet-1/4",
        "/network-instance default",
        "/routing-policy",
    ]
    content = []
    for section in sections:
        try:
            value = read_r4(section)
        except RuntimeError:
            value = "<section absent>"
        content.append((section, value))

    with target.open("w") as file:
        for section, value in content:
            file.write(f"### {section}\n{value}\n")
    print(f"[R4] Snapshot saved: {target}")
    return dict(content)

def verify_config():
    interface = read_r4("/interface ethernet-1/4")
    network = read_r4("/network-instance default")
    ospf = read_r4("/network-instance default protocols ospf")
    policy = read_r4("/routing-policy")

    for address in ("10.0.46.1/30", "2001:db8:5840:46::4/64"):
        if address not in interface:
            raise RuntimeError(f"Missing address: {address}")

    if "ethernet-1/4.0" not in network:
        raise RuntimeError("Missing network-instance attachment")

    for value in ("ospfv2", "ospfv3", "0.0.0.10", "ethernet-1/4.0"):
        if value not in ospf:
            raise RuntimeError(f"Missing OSPF configuration: {value}")

    for prefix in (
        "192.168.40.0/24",
        "192.168.50.0/24",
        "2001:db8:5840:40::/64",
        "2001:db8:5840:50::/64",
    ):
        if prefix not in policy:
            raise RuntimeError(f"Missing R4 BGP export prefix: {prefix}")

    print("[R4] Interface, OSPF and BGP export policy verified")

def check_neighbors(timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ipv4 = run([
            "docker", "exec", R6, "Cli", "-p", "15", "-c",
            "show ip ospf neighbor",
        ])
        ipv6 = run([
            "docker", "exec", R6, "Cli", "-p", "15", "-c",
            "show ipv6 ospf neighbor",
        ])
        if "FULL" in ipv4.upper() and "FULL" in ipv6.upper():
            print("[R4] OSPFv2 and OSPFv3 FULL")
            return
        time.sleep(5)
    raise RuntimeError("R4-R6 OSPFv2/OSPFv3 adjacency did not reach FULL")

def verify_rollback(before):
    for section, old_value in before.items():
        try:
            current = read_r4(section)
        except RuntimeError:
            current = "<section absent>"
        if current != old_value:
            raise RuntimeError(f"Rollback mismatch in {section}")
    print("[R4] Rollback verified")

def apply():
    try:
        verify_config()
        check_neighbors(timeout=20)
        print("[R4] Already configured; no changes necessary")
        return
    except RuntimeError:
        print("[R4] Day-2 configuration required")

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
        print("[R4] Confirmed commit submitted")

        verify_config()
        check_neighbors(timeout=120)

        cli("commit confirmed accept")
        accepted = True
        print("[R4] Confirmed commit ACCEPTED")
        print("[R4] Day-2 provisioning completed")

    except Exception as error:
        detail = str(error)
        if commit_attempted and not accepted:
            try:
                print("[R4] Rejecting confirmed commit")
                cli("commit confirmed reject")
                verify_rollback(before)
            except Exception as rollback_error:
                detail += (
                    "\nROLLBACK NOT VERIFIED: "
                    + str(rollback_error)
                    + "\nCheck R4 confirmed-commit state."
                )
        raise RuntimeError(detail) from error

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if args.apply:
        apply()
    else:
        print("[R4] DRY RUN")
        verify_config()
        check_neighbors(timeout=10)

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"[R4] ERROR: {error}", file=sys.stderr)
        sys.exit(1)
