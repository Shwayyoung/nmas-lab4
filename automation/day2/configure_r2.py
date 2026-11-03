#!/usr/bin/env python3
"""Day-2 provisioning for R2: R8 uplink plus redistribution updates."""

import argparse
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CONTAINER = "clab-5840-lab1-R2"
NEIGHBOR = "172.31.255.8"
CONFIG_PATH = "/mnt/flash/day2-r2.cfg"

CONFIG = """\
interface Ethernet3
   description L3_TO_R8
   no switchport
   ip address 10.0.28.1/30
   ipv6 address 2001:db8:5840:28::2/64
   ip ospf area 0.0.0.0
   ipv6 ospf 1 area 0.0.0.0
   no shutdown
!
ip prefix-list OSPF-TO-RIP
   seq 20 permit 192.168.40.0/24
   seq 30 permit 192.168.50.0/24
!
ipv6 prefix-list OSPFV3-TO-ISIS
   seq 20 permit 2001:db8:5840:40::/64
   seq 30 permit 2001:db8:5840:50::/64
!
router rip
   redistribute ospf match internal route-map OSPF-TO-RIP
!
router isis ACCESS
   redistribute ospfv3 match internal route-map OSPFV3-TO-ISIS
!
"""

INTERFACE_REQUIRED = (
    "description L3_TO_R8",
    "no switchport",
    "ip address 10.0.28.1/30",
    "ipv6 address 2001:db8:5840:28::2/64",
    "ip ospf area 0.0.0.0",
    "ipv6 ospf 1 area 0.0.0.0",
)


def run(args, timeout=60):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    output = result.stdout + result.stderr
    errors = ("% invalid", "% error", "% incomplete", "% ambiguous", "error:")
    if result.returncode or any(marker in output.lower() for marker in errors):
        raise RuntimeError(f"{args}\n{output}")
    return result.stdout

def cli(command, timeout=60):
    return run(
        ["docker", "exec", CONTAINER, "Cli", "-p", "15", "-c", command],
        timeout,
    )

def interface_section(running):
    match = re.search(
        r"(?m)^interface Ethernet3\s*$"
        r"(.*?)(?=^!$|^interface |\Z)",
        running,
        re.S | re.M,
    )
    return match.group(0) if match else ""

def missing_config():
    running = cli("show running-config")

    section = interface_section(running)
    section_lines = {line.strip() for line in section.splitlines()}

    missing = [
        item for item in INTERFACE_REQUIRED
        if item not in section_lines
    ]

    ipv4_prefix = cli(
        "show running-config section ip prefix-list OSPF-TO-RIP"
    )

    ipv6_prefix = cli(
        "show running-config section ipv6 prefix-list OSPFV3-TO-ISIS"
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
            "seq 20 permit 2001:db8:5840:40::/64",
            ipv6_prefix,
        ),
        (
            "seq 30 permit 2001:db8:5840:50::/64",
            ipv6_prefix,
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

    for expected, output in checks:
        if expected not in output:
            missing.append(expected)

    return missing

def verify_neighbors(timeout=120):
    deadline = time.monotonic() + timeout

    while True:
        ospfv2 = cli("show ip ospf neighbor")
        ospfv3 = cli("show ipv6 ospf neighbor")

        v2_ok = any(
            re.search(rf"^\s*{re.escape(NEIGHBOR)}\s", line)
            and "FULL" in line.upper()
            and "Ethernet3" in line
            for line in ospfv2.splitlines()
        )

        blocks = re.split(r"(?=^Neighbor\s+)", ospfv3, flags=re.MULTILINE)
        v3_ok = any(
            re.search(rf"^Neighbor\s+{re.escape(NEIGHBOR)}\b", block)
            and re.search(r"state is Full\b", block, re.IGNORECASE)
            and re.search(
                r"In area 0\.0\.0\.0 interface Ethernet3\b",
                block,
                re.IGNORECASE,
            )
            for block in blocks
        )

        if v2_ok and v3_ok:
            print("[R2] R2-R8 OSPFv2/OSPFv3: FULL")
            return

        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"R2-R8 adjacency not FULL: OSPFv2={v2_ok}, OSPFv3={v3_ok}\n"
                f"IPv4 neighbors:\n{ospfv2}\nIPv6 neighbors:\n{ospfv3}"
            )
        time.sleep(5)

def verify_legacy_routes(timeout=120):
    deadline = time.monotonic() + timeout
    while True:
        missing = []
        for device in ("S1", "S2"):
            ipv6 = run([
                "docker", "exec", f"clab-5840-lab1-{device}",
                "Cli", "-p", "15", "-c", "show ipv6 route"
            ])
            if "2001:db8:5840:50::/64" not in ipv6:
                missing.append(f"{device}: 2001:db8:5840:50::/64")
        if not missing:
            print("[R2] S1/S2 learned VLAN50 IPv6")
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("IPv6 redistribution verification failed: " + ", ".join(missing))
        time.sleep(5)

def apply():
    missing = missing_config()
    if not missing:
        print("[R2] configuration already present")
        verify_neighbors()
        verify_legacy_routes(timeout=20)
        return

    before = cli("show running-config")
    checkpoint = "day2_r2_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    cli(f"configure checkpoint save {checkpoint}")
    print(f"[R2] Checkpoint saved: {checkpoint}")

    attempted = False
    try:
        local = Path("/tmp/day2-r2.cfg")
        local.write_text(CONFIG)
        run(["docker", "cp", str(local), f"{CONTAINER}:{CONFIG_PATH}"])
        attempted = True

        cli("copy flash:day2-r2.cfg running-config", timeout=120)

        remaining = missing_config()
        if remaining:
            raise RuntimeError("Missing configuration: " + ", ".join(remaining))

        verify_neighbors()
        verify_legacy_routes()
        cli("copy running-config startup-config")
        print("[R2] CONFIGURED and saved")

    except Exception as error:
        detail = str(error)
        if attempted:
            try:
                print("[R2] Attempting rollback")
                cli(f"configure checkpoint restore {checkpoint}", timeout=120)
                after = cli("show running-config")
                if after != before:
                    raise RuntimeError("Running-config does not match pre-change snapshot")
                print("[R2] Rollback verified")
            except Exception as rollback_error:
                detail += f" | ROLLBACK FAILED: {rollback_error}"
        raise RuntimeError(detail) from error

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    missing = missing_config()
    if not missing:
        print("[R2] already configured")
        verify_neighbors()
        verify_legacy_routes(timeout=20)
        return

    print("[R2] missing configuration:")
    for item in missing:
        print(f"  {item}")

    if not args.apply:
        print("[R2] DRY RUN - no changes applied")
        return

    apply()

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[R2] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
