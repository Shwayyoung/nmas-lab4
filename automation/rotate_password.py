#!/usr/bin/env python3

import argparse
import ipaddress
import json
import os
import secrets
import shutil
import string
import sys
import tempfile
import time

from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from datetime import datetime, timezone

import paramiko

from credentials import get_password


NETBOX_URL = os.environ.get(
    "NETBOX_URL",
    "http://127.0.0.1:8000",
).rstrip("/")

NETBOX_TOKEN = os.environ.get("NETBOX_TOKEN")

SECRETS_FILE = Path("/etc/nmas-secrets")

ROTATION_LOG = Path("/var/log/nmas-password-rotation.log")

#write log
def write_rotation_log(
    device_name,
    status,
    message="",
):
    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    line = (
        f"{timestamp} "
        f"device={device_name} "
        f"status={status}"
    )

    if message:
        line += f" message={message}"

    with ROTATION_LOG.open(
        "a",
        encoding="utf-8",
    ) as log_file:
        log_file.write(line + "\n")

# ---------------------------------------------------------------------------
# NetBox
# ---------------------------------------------------------------------------

def normalize_ip(value):
    if not value:
        return None

    if "/" in value:
        return str(
            ipaddress.ip_interface(value).ip
        )

    return str(ipaddress.ip_address(value))


def netbox_get(path):
    if not NETBOX_TOKEN:
        raise RuntimeError(
            "NETBOX_TOKEN environment variable is not set"
        )

    req = Request(
        f"{NETBOX_URL}{path}",
        headers={
            "Authorization": f"Bearer {NETBOX_TOKEN}",
            "Accept": "application/json",
        },
    )

    with urlopen(req, timeout=10) as response:
        return json.loads(
            response.read().decode("utf-8")
        )


def get_device(device_name):
    data = netbox_get(
        "/api/dcim/devices/"
        f"?name={quote(device_name)}"
    )

    results = data.get("results", [])

    if len(results) != 1:
        raise RuntimeError(
            f"Expected one NetBox device named "
            f"{device_name}, found {len(results)}"
        )

    device = results[0]

    primary = (
        device.get("primary_ip4")
        or device.get("primary_ip6")
    )

    if not primary:
        raise RuntimeError(
            f"{device_name} has no primary management IP"
        )

    mgmt_ip = normalize_ip(
        primary.get("address")
    )

    custom = device.get(
        "custom_fields",
        {}
    )

    username = custom.get(
        "management_username"
    )

    secret_ref = custom.get(
        "secret_ref"
    )

    credential_profile = custom.get(
        "credential_profile"
    )

    if not username:
        raise RuntimeError(
            f"{device_name}: management_username "
            f"is not set in NetBox"
        )

    if not secret_ref:
        raise RuntimeError(
            f"{device_name}: secret_ref "
            f"is not set in NetBox"
        )

    device_type = (
        device.get("device_type")
        or {}
    )

    manufacturer = (
        device_type.get("manufacturer")
        or {}
    ).get("name", "")

    return {
        "name": device_name,
        "mgmt_ip": mgmt_ip,
        "username": username,
        "secret_ref": secret_ref,
        "credential_profile":
            credential_profile,
        "manufacturer":
            manufacturer.lower(),
    }


# ---------------------------------------------------------------------------
# Password generation
# ---------------------------------------------------------------------------

def generate_password(length=24):
    """
    Use a CLI-safe character set.

    Avoid quotes, ?, spaces, backslashes, etc.
    """

    alphabet = (
        string.ascii_letters
        + string.digits
        + "_-."
    )

    while True:
        password = "".join(
            secrets.choice(alphabet)
            for _ in range(length)
        )

        if (
            any(c.islower() for c in password)
            and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password)
        ):
            return password


# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------

def connect(host, username, password):
    client = paramiko.SSHClient()

    client.set_missing_host_key_policy(
        paramiko.AutoAddPolicy()
    )

    client.connect(
        hostname=host,
        username=username,
        password=password,
        look_for_keys=False,
        allow_agent=False,
        timeout=10,
        auth_timeout=10,
        banner_timeout=10,
    )

    return client


def read_channel(channel, delay=0.7):
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


def send_command(channel, command, delay=0.7):
    channel.send(command + "\n")

    return read_channel(
        channel,
        delay,
    )


# ---------------------------------------------------------------------------
# Arista EOS
# ---------------------------------------------------------------------------

def rotate_arista(
    device,
    old_password,
    new_password,
):

    client = connect(
        device["mgmt_ip"],
        device["username"],
        old_password,
    )

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
            (
                f"username "
                f"{device['username']} "
                f"secret 0 "
                f"{new_password}"
            ),
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
            value in output
            for value in bad_strings
        ):
            raise RuntimeError(
                "EOS returned a CLI error:\n"
                + output
            )

    finally:
        client.close()


# ---------------------------------------------------------------------------
# Nokia SR Linux
# ---------------------------------------------------------------------------

def rotate_srlinux(
    device,
    old_password,
    new_password,
):

    client = connect(
        device["mgmt_ip"],
        device["username"],
        old_password,
    )

    try:
        channel = client.invoke_shell()

        read_channel(channel)

        output = ""

        output += send_command(
            channel,
            "enter candidate",
        )

        username = device["username"]

        if username == "admin":
            command = (
                "system aaa authentication "
                "admin-user password "
                f"{new_password}"
            )

        else:
            command = (
                "system aaa authentication "
                f"user {username} "
                f"password {new_password}"
            )

        output += send_command(
            channel,
            command,
        )

        output += send_command(
            channel,
            "commit now",
            delay=2,
        )

        bad_strings = (
            "Error:",
            "Invalid",
            "failed",
            "Failed",
        )

        if any(
            value in output
            for value in bad_strings
        ):
            raise RuntimeError(
                "SR Linux returned a CLI error:\n"
                + output
            )

    finally:
        client.close()


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_new_password(
    device,
    new_password,
):
    client = connect(
        device["mgmt_ip"],
        device["username"],
        new_password,
    )

    client.close()


# ---------------------------------------------------------------------------
# Secret store update
# ---------------------------------------------------------------------------

def update_secret(
    secret_ref,
    new_password,
):
    if not SECRETS_FILE.exists():
        raise RuntimeError(
            f"{SECRETS_FILE} does not exist"
        )

    original = SECRETS_FILE.read_text()

    backup = Path(
        str(SECRETS_FILE) + ".bak"
    )

    shutil.copy2(
        SECRETS_FILE,
        backup,
    )

    lines = original.splitlines()

    replacement = (
        f"{secret_ref}="
        f"'{new_password}'"
    )

    found = False
    result = []

    for line in lines:

        stripped = line.strip()

        if stripped.startswith(
            f"{secret_ref}="
        ):
            result.append(replacement)
            found = True

        else:
            result.append(line)

    if not found:
        result.append(replacement)

    fd, temp_name = tempfile.mkstemp(
        prefix="nmas-secrets.",
        dir="/etc",
    )

    try:
        with os.fdopen(
            fd,
            "w",
        ) as temp_file:

            temp_file.write(
                "\n".join(result)
                + "\n"
            )

        os.chmod(
            temp_name,
            0o600,
        )

        os.replace(
            temp_name,
            SECRETS_FILE,
        )

    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


# ---------------------------------------------------------------------------
# Vendor selection
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
        "Unsupported manufacturer: "
        + manufacturer
    )

#get all device names
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
            ""
        ).lower()

        if (
            "arista" not in manufacturer
            and
            "nokia" not in manufacturer
        ):
            continue

        names.append(name)

    return sorted(names)

#change one device's password
def rotate_one(device_name, apply=False):

    device = get_device(
        device_name
    )

    platform = detect_platform(
        device
    )

    old_password = get_password(
        device["secret_ref"]
    )

    new_password = generate_password()

    print(
        f"\n=== {device_name} ==="
    )

    print(
        f"Management IP: "
        f"{device['mgmt_ip']}"
    )

    print(
        f"Username: "
        f"{device['username']}"
    )

    print(
        f"Platform: {platform}"
    )

    print(
        f"Secret ref: "
        f"{device['secret_ref']}"
    )

    if not apply:

        print(
            "DRY RUN: no changes made."
        )

        return True

    print(
        "Changing device password..."
    )

    if platform == "arista":

        rotate_arista(
            device,
            old_password,
            new_password,
        )

    elif platform == "nokia":

        rotate_srlinux(
            device,
            old_password,
            new_password,
        )

    print(
        "Verifying new SSH login..."
    )

    verify_new_password(
        device,
        new_password,
    )

    print(
        "New SSH login successful."
    )

    update_secret(
        device["secret_ref"],
        new_password,
    )

    print(
        "Password rotation completed."
    )

    write_rotation_log(
        device_name,
        "success",
    )

    return True

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():

    parser = argparse.ArgumentParser(
        description=(
            "Rotate network-device "
            "management passwords"
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
            "Rotate passwords for all "
            "supported network devices"
        ),
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Actually change passwords. "
            "Without this option, dry run."
        ),
    )

    args = parser.parse_args()

    # -------------------------------------------------------
    # Rotate all supported devices
    # -------------------------------------------------------

    if args.all:

        device_names = (
            get_all_device_names()
        )

        results = {}

        for device_name in device_names:

            try:

                rotate_one(
                    device_name,
                    apply=args.apply,
                )

                results[
                    device_name
                ] = "success"

            except Exception as exc:

                results[
                    device_name
                ] = (
                    f"failed: {exc}"
                )

                print(
                    f"{device_name}: "
                    f"FAILED: {exc}",
                    file=sys.stderr,
                )

                write_rotation_log(
                    device_name,
                    "failed",
                    str(exc),
                )
                
        print(
            "\n=== Summary ==="
        )

        for (
            device_name,
            result,
        ) in results.items():

            print(
                f"{device_name}: "
                f"{result}"
            )

        return

    # -------------------------------------------------------
    # Rotate one device
    # -------------------------------------------------------

    if not args.device:

        parser.error(
            "Specify DEVICE "
            "or use --all"
        )

    rotate_one(
        args.device,
        apply=args.apply,
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