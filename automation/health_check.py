#!/usr/bin/env python3

import argparse
import ipaddress
import json
import os
import subprocess
import sys
import time
import re
import csv
import io

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from concurrent.futures import ThreadPoolExecutor, as_completed

import paramiko

from credentials import get_password

ANSI_ESCAPE = re.compile(
    r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])"
)

NETBOX_URL = os.environ.get(
    "NETBOX_URL",
    "http://127.0.0.1:8000",
).rstrip("/")

NETBOX_TOKEN = os.environ.get("NETBOX_TOKEN")

REPORT_DIR = Path(
    "/home/student/labs/nmas-lab4/reports/health"
)

INFLUX_URL = os.environ.get(
    "INFLUX_URL",
    "http://127.0.0.1:8086",
).rstrip("/")

INFLUX_ORG = os.environ.get(
    "INFLUX_ORG",
    "5840Lab",
)

INFLUX_BUCKET = os.environ.get(
    "INFLUX_BUCKET",
    "nmas",
)

INFLUX_TOKEN = os.environ.get(
    "INFLUX_TOKEN"
)

# ---------------------------------------------------------------------------
# NetBox
# ---------------------------------------------------------------------------

def normalize_ip(value):

    if "/" in value:
        return str(
            ipaddress.ip_interface(value).ip
        )

    return str(
        ipaddress.ip_address(value)
    )


def netbox_get(path):

    if not NETBOX_TOKEN:
        raise RuntimeError(
            "NETBOX_TOKEN is not set"
        )

    request = Request(
        f"{NETBOX_URL}{path}",
        headers={
            "Authorization":
                f"Bearer {NETBOX_TOKEN}",
            "Accept":
                "application/json",
        },
    )

    with urlopen(
        request,
        timeout=10,
    ) as response:

        return json.loads(
            response.read().decode(
                "utf-8"
            )
        )


def get_device(device_name):

    data = netbox_get(
        "/api/dcim/devices/"
        f"?name={quote(device_name)}"
    )

    results = data.get(
        "results",
        [],
    )

    if len(results) != 1:

        raise RuntimeError(
            f"Device {device_name} "
            f"not uniquely found"
        )

    device = results[0]

    primary_ip = (
        device.get("primary_ip4")
        or device.get("primary_ip6")
    )

    if not primary_ip:

        raise RuntimeError(
            f"{device_name}: "
            f"no primary IP"
        )

    mgmt_ip = normalize_ip(
        primary_ip["address"]
    )

    custom = device.get(
        "custom_fields",
        {},
    )

    username = custom.get(
        "management_username"
    )

    secret_ref = custom.get(
        "secret_ref"
    )

    if not username:

        raise RuntimeError(
            f"{device_name}: "
            f"management_username missing"
        )

    if not secret_ref:

        raise RuntimeError(
            f"{device_name}: "
            f"secret_ref missing"
        )

    device_type = (
        device.get("device_type")
        or {}
    )

    manufacturer = (
        device_type.get("manufacturer")
        or {}
    ).get(
        "name",
        "",
    ).lower()

    return {
        "name": device_name,
        "mgmt_ip": mgmt_ip,
        "username": username,
        "secret_ref": secret_ref,
        "manufacturer": manufacturer,
    }


def get_all_device_names():

    data = netbox_get(
        "/api/dcim/devices/?limit=1000"
    )

    names = []

    for device in data.get(
        "results",
        [],
    ):

        name = device.get("name")

        if not name:
            continue

        device_type = (
            device.get("device_type")
            or {}
        )

        manufacturer = (
            device_type.get(
                "manufacturer"
            )
            or {}
        ).get(
            "name",
            "",
        ).lower()

        if (
            "arista" not in manufacturer
            and
            "nokia" not in manufacturer
        ):
            continue

        names.append(name)

    return sorted(names)


# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------

def detect_platform(device):

    manufacturer = (
        device["manufacturer"]
    )

    if "arista" in manufacturer:
        return "arista"

    if "nokia" in manufacturer:
        return "nokia"

    raise RuntimeError(
        f"Unsupported manufacturer: "
        f"{manufacturer}"
    )


# ---------------------------------------------------------------------------
# Local IP connectivity
# ---------------------------------------------------------------------------

def check_ping(ip_address):

    command = [
        "ping",
        "-c",
        "3",
        "-W",
        "2",
        ip_address,
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    return {
        "status":
            "pass"
            if result.returncode == 0
            else "fail",

        "reachable":
            result.returncode == 0,
    }


# ---------------------------------------------------------------------------
# SSH
# ---------------------------------------------------------------------------

def connect(device):

    password = get_password(
        device["secret_ref"]
    )

    client = paramiko.SSHClient()

    client.set_missing_host_key_policy(
        paramiko.AutoAddPolicy()
    )

    client.connect(
        hostname=device["mgmt_ip"],
        username=device["username"],
        password=password,
        look_for_keys=False,
        allow_agent=False,
        timeout=10,
        auth_timeout=10,
        banner_timeout=10,
    )

    return client


def read_channel(
    channel,
    delay=1,
):

    time.sleep(delay)

    output = ""

    while channel.recv_ready():

        output += channel.recv(
            65535
        ).decode(
            "utf-8",
            errors="replace",
        )

        time.sleep(0.1)

    return output

def run_shell_command(
    channel,
    command,
    timeout=8,
):
    """
    Send one interactive CLI command and wait until
    the device prompt returns.
    """
    channel.send(command + "\n")

    output = ""
    start = time.monotonic()
    last_data = start

    while True:

        if channel.recv_ready():

            chunk = channel.recv(
                65535
            ).decode(
                "utf-8",
                errors="replace",
            )

            output += chunk
            last_data = time.monotonic()

        else:

            if (
                output
                and
                time.monotonic() - last_data > 0.5
            ):

                clean = ANSI_ESCAPE.sub(
                    "",
                    output,
                )

                lines = [
                    line.strip()
                    for line in clean.splitlines()
                    if line.strip()
                ]

                if lines:
                    last_line = lines[-1]

                    if (
                        last_line.endswith("#")
                        or last_line.endswith(">")
                    ):
                        break

            time.sleep(0.05)

        if time.monotonic() - start > timeout:
            break

    return output

# ---------------------------------------------------------------------------
# Arista health check
# ---------------------------------------------------------------------------

def check_arista(device):

    client = connect(device)

    results = {}

    try:

        channel = (
            client.invoke_shell()
        )

        read_channel(channel)

        run_shell_command(
            channel,
            "terminal length 0",
        )

        # -------------------------------------------------------
        # Neighbor status
        # -------------------------------------------------------

        neighbor_output = (
            run_shell_command(
                channel,
                "show ip bgp summary",
            )
        )

        results["neighbors"] = {
            "command":
                "show ip bgp summary",
            "output":
                neighbor_output,
        }

        # -------------------------------------------------------
        # Routing table
        # -------------------------------------------------------

        route_output = (
            run_shell_command(
                channel,
                "show ip route",
            )
        )

        results["routing_table"] = {
            "command":
                "show ip route",
            "output":
                route_output,
        }

        # -------------------------------------------------------
        # CPU
        # -------------------------------------------------------

        cpu_output = (
            run_shell_command(
                channel,
                "show processes top once",
                #delay=2,
            )
        )

        results["cpu"] = {
            "command":
                "show processes top once",
            "output":
                cpu_output,
        }

    finally:

        client.close()

    return results


# ---------------------------------------------------------------------------
# Nokia SR Linux health check
# ---------------------------------------------------------------------------

def check_nokia(device):

    client = connect(device)

    results = {}

    try:

        channel = (
            client.invoke_shell()
        )

        read_channel(channel)

        # -------------------------------------------------------
        # Neighbor status
        # -------------------------------------------------------

        neighbor_output = (
            run_shell_command(
                channel,
                (
                    "show network-instance "
                    "default protocols bgp "
                    "neighbor"
                ),
                #delay=1.5,
            )
        )

        results["neighbors"] = {
            "command": (
                "show network-instance "
                "default protocols bgp "
                "neighbor"
            ),
            "output":
                neighbor_output,
        }

        # -------------------------------------------------------
        # Routing table
        # -------------------------------------------------------

        route_output = (
            run_shell_command(
                channel,
                (
                    "show network-instance default ipv4 route all"
                ),
                #delay=1.5,
            )
        )

        results["routing_table"] = {
            "command": (
                "show network-instance default ipv4 route all"
            ),
            "output":
                route_output,
        }

    finally:

        client.close()

    return results


# ---------------------------------------------------------------------------
# Device health
# ---------------------------------------------------------------------------

def check_device(device_name):

    print(
        f"\n=== Checking {device_name} ==="
    )

    device = get_device(
        device_name
    )

    platform = detect_platform(
        device
    )

    result = {
        "device":
            device_name,

        "management_ip":
            device["mgmt_ip"],

        "platform":
            platform,

        "timestamp":
            datetime.now(
                timezone.utc
            ).isoformat(),
    }

    # -----------------------------------------------------------
    # Ping
    # -----------------------------------------------------------

    print("Checking IP connectivity...")

    result["connectivity"] = (
        check_ping(
            device["mgmt_ip"]
        )
    )

    # -----------------------------------------------------------
    # SSH health checks
    # -----------------------------------------------------------

    print("Connecting via SSH...")

    try:

        if platform == "arista":

            checks = check_arista(
                device
            )

        else:

            checks = check_nokia(
                device
            )

        result.update(checks)

        result["ssh"] = {
            "status": "pass"
        }

        result["neighbor_health"] = (
            parse_bgp_health(
                checks["neighbors"]["output"],
                platform,
            )
        )

        result["route_health"] = (
            parse_route_health(
                checks["routing_table"]["output"],
                platform,
            )
        )

        if platform == "nokia":

            result["cpu_health"] = (
                get_cpu_from_influx(
                    device_name
                )
            )

        else:

            result["cpu_health"] = (
                parse_cpu_health(
                    checks["cpu"]["output"],
                    platform,
                )
            )

    except Exception as exc:

        result["ssh"] = {
            "status": "fail",
            "error": str(exc),
        }

    result["overall"] = (
        calculate_overall(result)
    )

    return result

def parse_bgp_health(output, platform):
    """
    Return:
    {
        "status": "pass|warn|fail|unknown",
        "established": N,
        "total": N
    }
    """

    text = output or ""

    if platform == "arista":
        total = 0
        established = 0

        for line in text.splitlines():
            line = line.strip()

            if not line:
                continue

            # BGP summary rows normally begin with neighbor IP
            if re.match(
                r"^[0-9a-fA-F:.]+\s+",
                line,
            ):
                total += 1

                # EOS established neighbor normally ends
                # with received-prefix count.
                fields = line.split()

                if fields and fields[-1].isdigit():
                    established += 1

    else:
        # SR Linux output commonly contains
        # "established" state text.
        total_match = re.search(
            r"(\d+)\s+configured neighbors",
            text,
            re.IGNORECASE,
        )

        established_match = re.search(
            r"(\d+)\s+configured sessions are established",
            text,
            re.IGNORECASE,
        )

        total = (
            int(total_match.group(1))
            if total_match
            else 0
        )

        established = (
            int(established_match.group(1))
            if established_match
            else 0
        )

    if total == 0:
        return {
            "status": "unknown",
            "established": 0,
            "total": 0,
        }

    if established == total:
        status = "pass"

    elif established == 0:
        status = "fail"

    else:
        status = "warn"

    return {
        "status": status,
        "established": established,
        "total": total,
    }

def parse_route_health(output, platform):

    text = output or ""

    if platform == "arista":

        count = 0

        for line in text.splitlines():

            line = line.strip()

            if re.match(
                r"^[A-Z*]+\s+"
                r"[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/",
                line,
            ):
                count += 1

    else:
        # SR Linux:
        # Route entries begin directly with an IPv4 prefix.
        #
        # Example:
        # 10.0.13.0/30  ospfv2 ...
        # 172.31.255.1/32 ospfv2 ...

        count = 0

        for line in text.splitlines():

            line = line.strip()

            if re.match(
                r"^\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}\s+",
                line,
            ):
                count += 1

    return {
        "status":
            "pass"
            if count > 0
            else "fail",

        "routes":
            count,
    }

def parse_cpu_health(output, platform):
    """
    Extract approximate CPU utilization.
    Thresholds:
      < 70%  PASS
      70-90  WARN
      > 90   FAIL
    """

    text = output or ""

    cpu = None

    # Try common percentage formats.
    patterns = [
        r"Cpu\(s\).*?(\d+(?:\.\d+)?)\s*id",
        r"CPU.*?(\d+(?:\.\d+)?)\s*%",
        r"utilization.*?(\d+(?:\.\d+)?)\s*%",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if not match:
            continue

        value = float(match.group(1))

        # top-like output reports idle percentage.
        if "id" in pattern:
            cpu = 100.0 - value
        else:
            cpu = value

        break

    if cpu is None:
        return {
            "status": "unknown",
            "percent": None,
        }

    cpu = round(cpu, 2)

    if cpu < 70:
        status = "pass"

    elif cpu <= 90:
        status = "warn"

    else:
        status = "fail"

    return {
        "status": status,
        "percent": cpu,
    }

def calculate_overall(result):

    statuses = [
        result.get(
            "connectivity",
            {},
        ).get("status"),

        result.get(
            "ssh",
            {},
        ).get("status"),

        result.get(
            "neighbor_health",
            {},
        ).get("status"),

        result.get(
            "route_health",
            {},
        ).get("status"),

        result.get(
            "cpu_health",
            {},
        ).get("status"),
    ]

    if "fail" in statuses:
        return "unhealthy"

    if (
        "warn" in statuses
        or "unknown" in statuses
        or None in statuses
    ):
        return "warning"

    return "healthy"

#check InfulxDB
def influx_query(flux):

    if not INFLUX_TOKEN:
        raise RuntimeError(
            "INFLUX_TOKEN is not set"
        )

    url = (
        f"{INFLUX_URL}/api/v2/query"
        f"?org={quote(INFLUX_ORG)}"
    )

    request = Request(
        url,
        data=flux.encode("utf-8"),
        headers={
            "Authorization":
                f"Token {INFLUX_TOKEN}",

            "Accept":
                "application/csv",

            "Content-Type":
                "application/vnd.flux",
        },
        method="POST",
    )

    with urlopen(
        request,
        timeout=10,
    ) as response:

        return response.read().decode(
            "utf-8"
        )

#get CPU from InfulxDB for Nokia SR Linux (R3 R4 R5)
def get_cpu_from_influx(
    device_name,
):

    measurement = (
        f"{device_name.lower()}_cpu"
    )

    flux = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -5m)
  |> filter(fn: (r) =>
       r._measurement == "{measurement}"
     )
  |> filter(fn: (r) =>
       r._field == "instant"
     )
  |> last()
  |> keep(columns: [
       "_time",
       "_value"
     ])
'''

    csv_text = influx_query(flux)

    reader = csv.DictReader(
        line
        for line in io.StringIO(
            csv_text
        )
        if not line.startswith("#")
        and line.strip()
    )

    for row in reader:

        value = row.get("_value")

        if value is None:
            continue

        cpu = float(value)

        if cpu < 70:
            status = "pass"

        elif cpu <= 90:
            status = "warn"

        else:
            status = "fail"

        return {
            "status": status,
            "percent": round(cpu, 2),
            "time": row.get("_time"),
            "source": "influxdb",
        }

    return {
        "status": "unknown",
        "percent": None,
        "source": "influxdb",
    }

# ---------------------------------------------------------------------------
# Save report
# ---------------------------------------------------------------------------

def save_report(results):

    REPORT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%d-%H%M%S"
    )

    filename = (
        REPORT_DIR
        / f"health-{timestamp}.json"
    )

    filename.write_text(
        json.dumps(
            results,
            indent=2,
        ),
        encoding="utf-8",
    )

    latest = (
        REPORT_DIR
        / "health-latest.json"
    )

    latest.write_text(
        json.dumps(
            results,
            indent=2,
        ),
        encoding="utf-8",
    )

    return filename


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():

    parser = argparse.ArgumentParser(
        description=(
            "NMAS network health check"
        )
    )

    parser.add_argument(
        "device",
        nargs="?",
        help="NetBox device name",
    )

    parser.add_argument(
        "--all",
        action="store_true",
        help=(
            "Check all supported "
            "network devices"
        ),
    )

    args = parser.parse_args()

    results = []

    if args.all:

        device_names = (
            get_all_device_names()
        )

    else:

        if not args.device:

            parser.error(
                "Specify DEVICE "
                "or use --all"
            )

        device_names = [
            args.device
        ]
    # -----------------------------------------------------------
    # Run health checks in parallel
    # -----------------------------------------------------------

    max_workers = min(
        6,
        len(device_names),
    )

    with ThreadPoolExecutor(
        max_workers=max_workers
    ) as executor:

        future_map = {
            executor.submit(
                check_device,
                device_name,
            ): device_name

            for device_name in device_names
        }

        for future in as_completed(
            future_map
        ):

            device_name = future_map[
                future
            ]

            try:

                result = future.result()

            except Exception as exc:

                result = {
                    "device":
                        device_name,

                    "timestamp":
                        datetime.now(
                            timezone.utc
                        ).isoformat(),

                    "status":
                        "error",

                    "error":
                        str(exc),
                }

            results.append(result)
    results.sort(
        key=lambda item:
            item.get("device", "")
    )

    filename = save_report(
        results
    )

    # -----------------------------------------------------------
    # Console summary
    # -----------------------------------------------------------

    print(
        "\n=== Health Check Summary ==="
    )

    for result in results:

        name = result.get(
            "device"
        )

        ping_status = (
            result.get(
                "connectivity",
                {},
            ).get(
                "status",
                "unknown",
            )
        )

        ssh_status = (
            result.get(
                "ssh",
                {},
            ).get(
                "status",
                "unknown",
            )
        )

        neighbor = result.get(
            "neighbor_health",
            {},
        )

        routes = result.get(
            "route_health",
            {},
        )

        cpu = result.get(
            "cpu_health",
            {},
        )

        overall = result.get(
            "overall",
            "unknown",
        )

        print(
            f"{name}: "
            f"PING={ping_status} "
            f"SSH={ssh_status} "
            f"BGP="
            f"{neighbor.get('established', '?')}/"
            f"{neighbor.get('total', '?')} "
            f"ROUTES="
            f"{routes.get('routes', '?')} "
            f"CPU="
            f"{cpu.get('percent', '?')}% "
            f"OVERALL="
            f"{overall.upper()}"
        )

    print(
        f"\nReport: {filename}"
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
