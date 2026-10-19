#!/usr/bin/env python3
'''
sudo bash
set -a
source /etc/default/netbox-bridge
set +a

cd /home/student/labs/nmas-lab4
python3 automation/interface_control.py \
  R1 Ethernet1 disable

python3 automation/interface_control.py \
  R1 Ethernet1 enable

python3 automation/interface_control.py \
  R3 ethernet-1/1 disable

python3 automation/interface_control.py \
  R3 ethernet-1/1 enable
'''
import argparse
import ipaddress
import json
import os
import sys
import time

from urllib.parse import quote
from urllib.request import Request, urlopen

import paramiko

from credentials import get_password


NETBOX_URL = os.environ.get(
    "NETBOX_URL",
    "http://127.0.0.1:8000",
).rstrip("/")

NETBOX_TOKEN = os.environ.get(
    "NETBOX_TOKEN"
)


# ------------------------------------------------------------
# NetBox helpers
# ------------------------------------------------------------

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
            f"no primary management IP"
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
        device_type.get(
            "manufacturer"
        )
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


# ------------------------------------------------------------
# Platform
# ------------------------------------------------------------

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


# ------------------------------------------------------------
# Safety
# ------------------------------------------------------------

def validate_interface(interface):

    value = interface.lower()

    blocked = {
        "management0",
        "management1",
        "mgmt0",
        "mgmt1",
    }

    if value in blocked:

        raise RuntimeError(
            f"Refusing to modify "
            f"management interface "
            f"{interface}"
        )


# ------------------------------------------------------------
# SSH
# ------------------------------------------------------------

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
    delay=0.8,
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


def send_command(
    channel,
    command,
    delay=0.8,
):

    channel.send(
        command + "\n"
    )

    return read_channel(
        channel,
        delay,
    )


# ------------------------------------------------------------
# Arista EOS
# ------------------------------------------------------------

def control_arista(
    device,
    interface,
    action,
):

    client = connect(device)

    try:

        channel = client.invoke_shell()

        read_channel(channel)

        output = ""

        output += send_command(
            channel,
            "enable",
        )

        output += send_command(
            channel,
            "configure terminal",
        )

        output += send_command(
            channel,
            f"interface {interface}",
        )

        if action == "disable":

            output += send_command(
                channel,
                "shutdown",
            )

        else:

            output += send_command(
                channel,
                "no shutdown",
            )

        output += send_command(
            channel,
            "end",
        )

        output += send_command(
            channel,
            "write memory",
            delay=1.5,
        )

        bad_strings = (
            "% Invalid",
            "% Error",
            "Incomplete command",
            "Ambiguous command",
        )

        if any(
            item in output
            for item in bad_strings
        ):

            raise RuntimeError(
                "EOS returned a CLI error:\n"
                + output
            )

        return output

    finally:

        client.close()


# ------------------------------------------------------------
# Nokia SR Linux
# ------------------------------------------------------------

def control_nokia(
    device,
    interface,
    action,
):

    client = connect(device)

    try:

        channel = client.invoke_shell()

        read_channel(channel)

        output = ""

        output += send_command(
            channel,
            "enter candidate",
        )

        state = (
            "disable"
            if action == "disable"
            else "enable"
        )

        output += send_command(
            channel,
            (
                f"interface {interface} "
                f"admin-state {state}"
            ),
        )

        output += send_command(
            channel,
            "commit now",
            delay=1.5,
        )

        bad_strings = (
            "Parsing error",
            "Error:",
            "Invalid",
            "failed",
            "Failed",
        )

        if any(
            item in output
            for item in bad_strings
        ):

            raise RuntimeError(
                "SR Linux returned "
                "a CLI error:\n"
                + output
            )

        return output

    finally:

        client.close()


# ------------------------------------------------------------
# Main control
# ------------------------------------------------------------

def control_interface(
    device_name,
    interface,
    action,
):

    validate_interface(
        interface
    )

    device = get_device(
        device_name
    )

    platform = detect_platform(
        device
    )

    print(
        f"Device: {device_name}"
    )

    print(
        f"Interface: {interface}"
    )

    print(
        f"Action: {action}"
    )

    print(
        f"Platform: {platform}"
    )

    if platform == "arista":

        control_arista(
            device,
            interface,
            action,
        )

    elif platform == "nokia":

        control_nokia(
            device,
            interface,
            action,
        )

    print(
        f"SUCCESS: {device_name} "
        f"{interface} {action}"
    )


def main():

    parser = argparse.ArgumentParser(
        description=(
            "Enable or disable "
            "a network interface"
        )
    )

    parser.add_argument(
        "device",
        help="NetBox device name",
    )

    parser.add_argument(
        "interface",
        help="Interface name",
    )

    parser.add_argument(
        "action",
        choices=[
            "enable",
            "disable",
        ],
    )

    args = parser.parse_args()

    control_interface(
        args.device,
        args.interface,
        args.action,
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