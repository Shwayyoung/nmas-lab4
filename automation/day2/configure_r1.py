#!/usr/bin/env python3
"""Day-2 provisioning for R1 redistribution toward the legacy access network."""

import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CONTAINER = "clab-5840-lab1-R1"
CONFIG_PATH = "/mnt/flash/day2-r1.cfg"

CONFIG = """\
ip prefix-list OSPF-TO-RIP
   seq 20 permit 192.168.40.0/24
   seq 30 permit 192.168.50.0/24
!
ipv6 prefix-list OSPFV3-TO-ISIS
   seq 10 permit 2001:db8:5840:50::/64
!
route-map OSPFV3-TO-ISIS permit 10
   match ipv6 address prefix-list OSPFV3-TO-ISIS
!
router rip
   redistribute ospf match internal route-map OSPF-TO-RIP
!
router isis ACCESS
   redistribute ospfv3 match internal route-map OSPFV3-TO-ISIS
!
"""


def run(args, timeout=60):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    output = result.stdout + result.stderr
    markers = ("% invalid", "% error", "% incomplete", "% ambiguous", "error:")
    if result.returncode or any(x in output.lower() for x in markers):
        raise RuntimeError(f"{args}\n{output}")
    return result.stdout

def cli(command, timeout=60):
    return run(
        ["docker", "exec", CONTAINER, "Cli", "-p", "15", "-c", command],
        timeout,
    )

def missing_config():
    ipv4_prefix = cli(
        "show running-config section ip prefix-list OSPF-TO-RIP"
    )

    ipv6_prefix = cli(
        "show running-config section ipv6 prefix-list OSPFV3-TO-ISIS"
    )

    route_map = cli(
        "show running-config section route-map"
    )

    rip = cli(
        "show running-config section router rip"
    )

    isis = cli(
        "show running-config section router isis"
    )

    checks = [
        (
            "seq 20 permit 192.168.40.0/24",
            ipv4_prefix,
        ),
        (
            "seq 30 permit 192.168.50.0/24",
            ipv4_prefix,
        ),
        (
            "seq 10 permit 2001:db8:5840:50::/64",
            ipv6_prefix,
        ),
        (
            "route-map OSPFV3-TO-ISIS permit 10",
            route_map,
        ),
        (
            "match ipv6 address prefix-list OSPFV3-TO-ISIS",
            route_map,
        ),
        (
            "redistribute ospf match internal route-map OSPF-TO-RIP",
            rip,
        ),
        (
            "redistribute ospfv3 match internal route-map OSPFV3-TO-ISIS",
            isis,
        ),
    ]

    return [
        expected
        for expected, output in checks
        if expected not in output
    ]

def verify_routes(timeout=120):
    deadline = time.monotonic() + timeout
    while True:
        missing = []
        for device in ("S1", "S2"):
            ipv4 = run([
                "docker", "exec", f"clab-5840-lab1-{device}",
                "Cli", "-p", "15", "-c", "show ip route"
            ])
            ipv6 = run([
                "docker", "exec", f"clab-5840-lab1-{device}",
                "Cli", "-p", "15", "-c", "show ipv6 route"
            ])
            for prefix in ("192.168.40.0/24", "192.168.50.0/24"):
                if prefix not in ipv4:
                    missing.append(f"{device}: {prefix}")
            if "2001:db8:5840:50::/64" not in ipv6:
                missing.append(f"{device}: 2001:db8:5840:50::/64")

        if not missing:
            print("[R1] S1/S2 learned VLAN40/50 IPv4 and VLAN50 IPv6")
            return

        if time.monotonic() >= deadline:
            raise RuntimeError("Route propagation verification failed: " + ", ".join(missing))
        time.sleep(5)

def apply():
    missing = missing_config()
    if not missing:
        print("[R1] Day-2 configuration already present")
        verify_routes(timeout=20)
        return

    before = cli("show running-config")
    checkpoint = "day2_r1_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    cli(f"configure checkpoint save {checkpoint}")
    print(f"[R1] Checkpoint saved: {checkpoint}")

    attempted = False
    try:
        local = Path("/tmp/day2-r1.cfg")
        local.write_text(CONFIG)
        run(["docker", "cp", str(local), f"{CONTAINER}:{CONFIG_PATH}"])
        attempted = True

        cli("copy flash:day2-r1.cfg running-config", timeout=120)

        remaining = missing_config()
        if remaining:
            raise RuntimeError("Missing configuration: " + ", ".join(remaining))

        verify_routes()
        cli("copy running-config startup-config")
        print("[R1] CONFIGURED and saved")

    except Exception as error:
        detail = str(error)
        if attempted:
            try:
                print("[R1] Attempting rollback")
                cli(f"configure checkpoint restore {checkpoint}", timeout=120)
                after = cli("show running-config")
                if after != before:
                    raise RuntimeError("Running-config does not match pre-change snapshot")
                print("[R1] Rollback verified")
            except Exception as rollback_error:
                detail += f" | ROLLBACK FAILED: {rollback_error}"
        raise RuntimeError(detail) from error

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    missing = missing_config()
    if not missing:
        print("[R1] already configured")
        verify_routes(timeout=20)
        return

    print("[R1] missing configuration:")
    for item in missing:
        print(f"  {item}")

    if not args.apply:
        print("[R1] DRY RUN - no changes applied")
        return

    apply()

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[R1] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
