#!/usr/bin/env python3

import argparse
import ipaddress
import json
import os
import secrets
import shutil
import socket
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

BOOTSTRAP_SECRET_REFS = {
    "arista": "ARISTA_BOOTSTRAP_PASSWORD",
    "nokia": "NOKIA_BOOTSTRAP_PASSWORD",
}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def write_rotation_log(device_name, status, message=""):
    timestamp = datetime.now(timezone.utc).isoformat()

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
        return str(ipaddress.ip_interface(value).ip)

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

    mgmt_ip = normalize_ip(primary.get("address"))
    custom = device.get("custom_fields", {})

    username = custom.get("management_username")
    secret_ref = custom.get("secret_ref")
    credential_profile = custom.get("credential_profile")

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

    device_type = device.get("device_type") or {}
    manufacturer = (
        device_type.get("manufacturer") or {}
    ).get("name", "")

    return {
        "name": device_name,
        "mgmt_ip": mgmt_ip,
        "username": username,
        "secret_ref": secret_ref,
        "credential_profile": credential_profile,
        "manufacturer": manufacturer.lower(),
    }


# ---------------------------------------------------------------------------
# Password generation
# ---------------------------------------------------------------------------

def generate_password(length=24):
    """Generate a CLI-safe random password."""

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

def _interactive_connect(host, username, password):
    """Connect using SSH keyboard-interactive authentication."""

    sock = socket.create_connection(
        (host, 22),
        timeout=10,
    )

    transport = paramiko.Transport(sock)
    transport.banner_timeout = 10
    transport.auth_timeout = 10

    try:
        transport.start_client(timeout=10)

        def handler(title, instructions, prompt_list):
            return [password for _prompt, _echo in prompt_list]

        transport.auth_interactive(
            username,
            handler,
        )

        if not transport.is_authenticated():
            raise paramiko.AuthenticationException(
                "keyboard-interactive authentication failed"
            )

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(
            paramiko.AutoAddPolicy()
        )
        client._transport = transport
        return client

    except Exception:
        transport.close()
        raise


def connect(host, username, password):
    """
    Connect using password authentication first, then automatically fall back
    to keyboard-interactive. This supports both the older lab nodes and the
    newly deployed cEOS nodes.
    """

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(
        paramiko.AutoAddPolicy()
    )

    try:
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

    except paramiko.AuthenticationException as password_error:
        client.close()

        try:
            return _interactive_connect(
                host,
                username,
                password,
            )
        except paramiko.AuthenticationException as interactive_error:
            raise paramiko.AuthenticationException(
                "password and keyboard-interactive authentication failed"
            ) from interactive_error
        except Exception:
            raise password_error


def read_channel(channel, delay=0.7):
    time.sleep(delay)
    output = ""

    while channel.recv_ready():
        output += channel.recv(65535).decode(
            "utf-8",
            errors="replace",
        )
        time.sleep(0.1)

    return output


def send_command(channel, command, delay=0.7):
    channel.send(command + "\n")
    return read_channel(channel, delay)


def open_old_session(device, platform, bootstrap=False):
    """
    Open the recovery session used for the whole rotation transaction.

    Normal mode:
      only the per-device secret_ref is tried.

    Bootstrap mode:
      try the per-device secret first; if authentication fails or the secret
      does not yet exist, try the platform bootstrap password.
    """

    candidates = []
    secret_error = None

    try:
        current_password = get_password(
            device["secret_ref"]
        )
        candidates.append(
            ("secret-store", current_password)
        )
    except Exception as exc:
        secret_error = exc
        if not bootstrap:
            raise

    if bootstrap:
        bootstrap_ref = BOOTSTRAP_SECRET_REFS[platform]
        bootstrap_password = get_password(bootstrap_ref)

        if not any(
            password == bootstrap_password
            for _source, password in candidates
        ):
            candidates.append(
                ("bootstrap", bootstrap_password)
            )

    last_auth_error = None

    for source, password in candidates:
        try:
            client = connect(
                device["mgmt_ip"],
                device["username"],
                password,
            )

            if source == "bootstrap":
                print(
                    "Stored device password did not authenticate; "
                    "bootstrap password accepted."
                )

            return client, password, source

        except paramiko.AuthenticationException as exc:
            last_auth_error = exc

            if source == "secret-store" and bootstrap:
                print(
                    "Stored device password authentication failed; "
                    "trying bootstrap password..."
                )
                continue

            raise

    if last_auth_error:
        raise last_auth_error

    if secret_error:
        raise secret_error

    raise RuntimeError(
        f"{device['name']}: no usable password candidate found"
    )


# ---------------------------------------------------------------------------
# Arista EOS transaction helpers
# ---------------------------------------------------------------------------

def arista_change_password(device, client, new_password):
    channel = client.invoke_shell()
    read_channel(channel)

    output = ""
    output += send_command(channel, "enable")
    output += send_command(channel, "configure terminal")
    output += send_command(
        channel,
        (
            f"username {device['username']} "
            f"secret 0 {new_password}"
        ),
    )
    output += send_command(channel, "end")

    bad_strings = (
        "% Invalid",
        "% Error",
        "Incomplete command",
        "Ambiguous command",
    )

    if any(value in output for value in bad_strings):
        raise RuntimeError(
            "EOS returned a CLI error:\n" + output
        )

    return channel


def arista_save(channel):
    output = send_command(
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

    if any(value in output for value in bad_strings):
        raise RuntimeError(
            "EOS write memory failed:\n" + output
        )


def arista_rollback(device, channel, old_password):
    output = ""
    output += send_command(channel, "configure terminal")
    output += send_command(
        channel,
        (
            f"username {device['username']} "
            f"secret 0 {old_password}"
        ),
    )
    output += send_command(channel, "end")
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

    if any(value in output for value in bad_strings):
        raise RuntimeError(
            "EOS rollback failed:\n" + output
        )


# ---------------------------------------------------------------------------
# Nokia SR Linux transaction helpers
# ---------------------------------------------------------------------------

def _srl_password_command(device, password):
    username = device["username"]

    if username == "admin":
        return (
            "system aaa authentication "
            "admin-user password "
            f"{password}"
        )

    return (
        "system aaa authentication "
        f"user {username} "
        f"password {password}"
    )


def srlinux_change_password(device, client, new_password):
    channel = client.invoke_shell()
    read_channel(channel)

    output = ""
    output += send_command(channel, "enter candidate")
    output += send_command(
        channel,
        _srl_password_command(device, new_password),
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

    if any(value in output for value in bad_strings):
        raise RuntimeError(
            "SR Linux returned a CLI error:\n" + output
        )

    return channel


def srlinux_rollback(device, channel, old_password):
    output = ""
    output += send_command(channel, "enter candidate")
    output += send_command(
        channel,
        _srl_password_command(device, old_password),
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

    if any(value in output for value in bad_strings):
        raise RuntimeError(
            "SR Linux rollback failed:\n" + output
        )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_password(device, password):
    client = connect(
        device["mgmt_ip"],
        device["username"],
        password,
    )
    return client


# ---------------------------------------------------------------------------
# Secret store update
# ---------------------------------------------------------------------------

def update_secret(secret_ref, new_password):
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
        with os.fdopen(fd, "w") as temp_file:
            temp_file.write(
                "\n".join(result) + "\n"
            )

        os.chmod(temp_name, 0o600)
        os.replace(temp_name, SECRETS_FILE)

    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


# ---------------------------------------------------------------------------
# Vendor selection
# ---------------------------------------------------------------------------

def detect_platform(device):
    manufacturer = device["manufacturer"]

    if "arista" in manufacturer:
        return "arista"

    if "nokia" in manufacturer:
        return "nokia"

    raise RuntimeError(
        "Unsupported manufacturer: "
        + manufacturer
    )


def get_all_device_names():
    data = netbox_get(
        "/api/dcim/devices/?limit=1000"
    )

    names = []

    for device in data.get("results", []):
        name = device.get("name")

        if not name:
            continue

        device_type = device.get("device_type") or {}
        manufacturer = (
            device_type.get("manufacturer") or {}
        ).get("name", "").lower()

        if (
            "arista" not in manufacturer
            and "nokia" not in manufacturer
        ):
            continue

        names.append(name)

    return sorted(names)


# ---------------------------------------------------------------------------
# Rotation transaction
# ---------------------------------------------------------------------------

def rotate_one(
    device_name,
    apply=False,
    bootstrap=False,
):
    device = get_device(device_name)
    platform = detect_platform(device)

    print(f"\n=== {device_name} ===")
    print(f"Management IP: {device['mgmt_ip']}")
    print(f"Username: {device['username']}")
    print(f"Platform: {platform}")
    print(f"Secret ref: {device['secret_ref']}")
    print(
        "Mode: "
        + ("BOOTSTRAP" if bootstrap else "NORMAL")
    )

    if not apply:
        print("DRY RUN: no changes made.")
        return True

    old_client = None
    old_channel = None
    new_client = None
    old_password = None
    old_source = None
    password_changed = False
    secret_updated = False

    try:
        print("Opening recovery SSH session...")

        old_client, old_password, old_source = (
            open_old_session(
                device,
                platform,
                bootstrap=bootstrap,
            )
        )

        print(
            "Current password authenticated "
            f"using source: {old_source}"
        )

        new_password = generate_password()

        print("Changing device password...")

        if platform == "arista":
            old_channel = arista_change_password(
                device,
                old_client,
                new_password,
            )
        elif platform == "nokia":
            old_channel = srlinux_change_password(
                device,
                old_client,
                new_password,
            )

        password_changed = True

        print(
            "Password changed; recovery session remains open."
        )
        print("Verifying new SSH login in a second session...")

        new_client = verify_password(
            device,
            new_password,
        )

        print("New SSH login successful.")

        if platform == "arista":
            print("Saving EOS configuration...")
            arista_save(old_channel)

        print("Updating /etc/nmas-secrets...")
        update_secret(
            device["secret_ref"],
            new_password,
        )
        secret_updated = True

        print("Password rotation completed.")

        write_rotation_log(
            device_name,
            "success",
            f"source={old_source}",
        )

        return True

    except Exception as original_error:
        rollback_error = None

        if password_changed and old_channel is not None:
            print(
                "Rotation failed after password change; "
                "rolling back through recovery session...",
                file=sys.stderr,
            )

            try:
                if platform == "arista":
                    arista_rollback(
                        device,
                        old_channel,
                        old_password,
                    )
                elif platform == "nokia":
                    srlinux_rollback(
                        device,
                        old_channel,
                        old_password,
                    )

                print(
                    "Device password rollback completed.",
                    file=sys.stderr,
                )

                rollback_verify = verify_password(
                    device,
                    old_password,
                )
                rollback_verify.close()

                print(
                    "Old password verified after rollback.",
                    file=sys.stderr,
                )

                # In bootstrap mode the per-device secret may contain a stale
                # password from an older ContainerLab deployment. If the real
                # old password came from the bootstrap secret, reconcile the
                # per-device secret after rolling the device back.
                if (
                    bootstrap
                    and old_source == "bootstrap"
                    and not secret_updated
                ):
                    update_secret(
                        device["secret_ref"],
                        old_password,
                    )
                    print(
                        "Per-device secret reconciled to bootstrap password "
                        "after rollback.",
                        file=sys.stderr,
                    )

            except Exception as exc:
                rollback_error = exc

        message = str(original_error)

        if rollback_error is not None:
            message += (
                " | rollback FAILED: "
                + str(rollback_error)
            )

        write_rotation_log(
            device_name,
            "failed",
            message,
        )

        if rollback_error is not None:
            raise RuntimeError(
                f"{original_error}; rollback also failed: "
                f"{rollback_error}"
            ) from original_error

        raise

    finally:
        if new_client is not None:
            new_client.close()

        if old_client is not None:
            old_client.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Rotate network-device management passwords"
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
            "Rotate passwords for all supported "
            "network devices"
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

    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help=(
            "If the stored per-device password does not authenticate, "
            "fall back to ARISTA_BOOTSTRAP_PASSWORD or "
            "NOKIA_BOOTSTRAP_PASSWORD from /etc/nmas-secrets. "
            "Use this after a fresh ContainerLab deployment."
        ),
    )

    args = parser.parse_args()

    if args.all and args.device:
        parser.error(
            "Specify either DEVICE or --all, not both"
        )

    if args.all:
        device_names = get_all_device_names()
        results = {}

        for device_name in device_names:
            try:
                rotate_one(
                    device_name,
                    apply=args.apply,
                    bootstrap=args.bootstrap,
                )
                results[device_name] = "success"

            except Exception as exc:
                results[device_name] = (
                    f"failed: {exc}"
                )

                print(
                    f"{device_name}: FAILED: {exc}",
                    file=sys.stderr,
                )

        print("\n=== Summary ===")

        for device_name, result in results.items():
            print(f"{device_name}: {result}")

        if any(
            result != "success"
            for result in results.values()
        ):
            sys.exit(1)

        return

    if not args.device:
        parser.error(
            "Specify DEVICE or use --all"
        )

    rotate_one(
        args.device,
        apply=args.apply,
        bootstrap=args.bootstrap,
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
