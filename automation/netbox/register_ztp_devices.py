#!/usr/bin/env python3
"""Idempotently register ZTP devices, hosts, Day-2 interfaces, IPs and cables in NetBox."""

import os
import sys
import requests

NETBOX_URL = os.getenv(
    "NETBOX_URL",
    "http://127.0.0.1:8000",
).rstrip("/")

TOKEN = os.getenv("NETBOX_TOKEN")

if not TOKEN:
    print(
        "[NetBox] ERROR: NETBOX_TOKEN is not set",
        file=sys.stderr,
    )
    sys.exit(1)

HEADERS = {
    "Authorization": f"Token {TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

SITE_NAME = "Advanced Network Automation Lab 1"

CEOS_DEVICE_TYPE = "cEOS 4.36.2"
HOST_DEVICE_TYPE = "3.24.1"


# ============================================================
# ZTP network devices
# ============================================================

DEVICES = {
    "R6": {
        "role": "Router",
        "device_type": CEOS_DEVICE_TYPE,
        "secret_ref": "R6_PASSWORD",
        "primary4": "172.20.20.16/24",
        "primary6": "3fff:172:20:20::10/64",
        "interfaces": {
            "Ethernet1": {
                "type": "1000base-t",
                "ips": [
                    "10.0.46.2/30",
                    "2001:db8:5840:46::6/64",
                ],
            },
            "Ethernet2": {
                "type": "1000base-t",
                "ips": [
                    "10.0.67.1/30",
                    "2001:db8:5840:67::6/64",
                ],
            },
            "Loopback0": {
                "type": "virtual",
                "ips": [
                    "172.31.255.6/32",
                    "2001:db8:5840:ffff::6/128",
                ],
            },
            "Management0": {
                "type": "virtual",
                "ips": [
                    "172.20.20.16/24",
                ],
            },
            "Ma0": {
                "type": "virtual",
                "ips": [
                    "3fff:172:20:20::10/64",
                ],
            },
        },
    },

    "R7": {
        "role": "Router",
        "device_type": CEOS_DEVICE_TYPE,
        "secret_ref": "R7_PASSWORD",
        "primary4": "172.20.20.17/24",
        "primary6": "3fff:172:20:20::11/64",
        "interfaces": {
            "Ethernet1": {
                "type": "1000base-t",
                "ips": [
                    "10.0.67.2/30",
                    "2001:db8:5840:67::7/64",
                ],
            },
            "Ethernet2": {
                "type": "1000base-t",
                "ips": [
                    "192.168.40.1/24",
                    "2001:db8:5840:40::1/64",
                ],
            },
            "Loopback0": {
                "type": "virtual",
                "ips": [
                    "172.31.255.7/32",
                    "2001:db8:5840:ffff::7/128",
                ],
            },
            "Management0": {
                "type": "virtual",
                "ips": [
                    "172.20.20.17/24",
                ],
            },
            "Ma0": {
                "type": "virtual",
                "ips": [
                    "3fff:172:20:20::11/64",
                ],
            },
        },
    },

    "R8": {
        "role": "Router",
        "device_type": CEOS_DEVICE_TYPE,
        "secret_ref": "R8_PASSWORD",
        "primary4": "172.20.20.19/24",
        "primary6": "3fff:172:20:20::13/64",
        "interfaces": {
            "Ethernet1": {
                "type": "1000base-t",
                "ips": [
                    "10.0.28.2/30",
                    "2001:db8:5840:28::8/64",
                ],
            },
            "Ethernet2": {
                "type": "1000base-t",
                "ips": [
                    "192.168.50.1/24",
                    "2001:db8:5840:50::1/64",
                ],
            },
            "Loopback0": {
                "type": "virtual",
                "ips": [
                    "172.31.255.8/32",
                    "2001:db8:5840:ffff::8/128",
                ],
            },
            "Management0": {
                "type": "virtual",
                "ips": [
                    "172.20.20.19/24",
                ],
            },
            "Ma0": {
                "type": "virtual",
                "ips": [
                    "3fff:172:20:20::13/64",
                ],
            },
        },
    },

    "S5": {
        "role": "Switch",
        "device_type": CEOS_DEVICE_TYPE,
        "secret_ref": "S5_PASSWORD",
        "primary4": "172.20.20.20/24",
        "primary6": "3fff:172:20:20::14/64",
        "interfaces": {
            "Ethernet1": {
                "type": "1000base-t",
                "ips": [],
            },
            "Ethernet2": {
                "type": "1000base-t",
                "ips": [],
            },
            "Ethernet3": {
                "type": "1000base-t",
                "ips": [],
            },
            "Management0": {
                "type": "virtual",
                "ips": [
                    "172.20.20.20/24",
                ],
            },
            "Ma0": {
                "type": "virtual",
                "ips": [
                    "3fff:172:20:20::14/64",
                ],
            },
        },
    },
}


# ============================================================
# New Alpine hosts
# ============================================================

HOSTS = {
    "H5": {
        "role": "Host",
        "device_type": HOST_DEVICE_TYPE,
        "primary4": "172.20.20.18/24",
        "primary6": "3fff:172:20:20::12/64",
        "interfaces": {
            "eth0": {
                "type": "virtual",
                "ips": [
                    "172.20.20.18/24",
                    "3fff:172:20:20::12/64",
                ],
            },
            "eth1": {
                "type": "1000base-t",
                "ips": [
                    "192.168.40.105/24",
                    "2001:db8:5840:40::105/64",
                ],
            },
        },
    },

    "H6": {
        "role": "Host",
        "device_type": HOST_DEVICE_TYPE,
        "primary4": "172.20.20.21/24",
        "primary6": "3fff:172:20:20::15/64",
        "interfaces": {
            "eth0": {
                "type": "virtual",
                "ips": [
                    "172.20.20.21/24",
                    "3fff:172:20:20::15/64",
                ],
            },
            "eth1": {
                "type": "1000base-t",
                "ips": [
                    "192.168.50.106/24",
                    "2001:db8:5840:50::106/64",
                ],
            },
        },
    },

    "H7": {
        "role": "Host",
        "device_type": HOST_DEVICE_TYPE,
        "primary4": "172.20.20.22/24",
        "primary6": "3fff:172:20:20::16/64",
        "interfaces": {
            "eth0": {
                "type": "virtual",
                "ips": [
                    "172.20.20.22/24",
                    "3fff:172:20:20::16/64",
                ],
            },
            "eth1": {
                "type": "1000base-t",
                "ips": [
                    "192.168.50.107/24",
                    "2001:db8:5840:50::107/64",
                ],
            },
        },
    },
}


# ============================================================
# Interfaces added to existing devices by Day-2
# ============================================================

DAY2_INTERFACES = {
    "R2": {
        "Ethernet3": {
            "type": "1000base-t",
            "ips": [
                "10.0.28.1/30",
                "2001:db8:5840:28::2/64",
            ],
        },
    },

    "R4": {
        "ethernet-1/4": {
            "type": "1000base-t",
            "ips": [
                "10.0.46.1/30",
                "2001:db8:5840:46::4/64",
            ],
        },
    },
}


# ============================================================
# Physical topology
# ============================================================

CABLES = [
    (
        ("R4", "ethernet-1/4"),
        ("R6", "Ethernet1"),
    ),
    (
        ("R6", "Ethernet2"),
        ("R7", "Ethernet1"),
    ),
    (
        ("R7", "Ethernet2"),
        ("H5", "eth1"),
    ),
    (
        ("R2", "Ethernet3"),
        ("R8", "Ethernet1"),
    ),
    (
        ("R8", "Ethernet2"),
        ("S5", "Ethernet1"),
    ),
    (
        ("S5", "Ethernet2"),
        ("H6", "eth1"),
    ),
    (
        ("S5", "Ethernet3"),
        ("H7", "eth1"),
    ),
]


# ============================================================
# API helpers
# ============================================================

def api(method, path, **kwargs):
    url = f"{NETBOX_URL}/api/{path.lstrip('/')}"

    response = requests.request(
        method,
        url,
        headers=HEADERS,
        timeout=30,
        **kwargs,
    )

    if not response.ok:
        raise RuntimeError(
            f"{method} {url} -> {response.status_code}\n"
            f"{response.text}"
        )

    if response.status_code == 204:
        return None

    return response.json()


def one(path, **params):
    data = api(
        "GET",
        path,
        params=params,
    )

    results = data.get("results", [])

    if len(results) != 1:
        raise RuntimeError(
            f"Expected one result for {path} "
            f"{params}, got {len(results)}"
        )

    return results[0]


def maybe_one(path, **params):
    data = api(
        "GET",
        path,
        params=params,
    )

    results = data.get("results", [])

    if not results:
        return None

    if len(results) > 1:
        raise RuntimeError(
            f"Expected at most one result for "
            f"{path} {params}, got {len(results)}"
        )

    return results[0]


# ============================================================
# Device
# ============================================================

def ensure_device(
    name,
    spec,
    site_id,
    device_type_id,
    role_id,
    custom_fields=None,
):
    existing = maybe_one(
        "dcim/devices/",
        name=name,
    )

    payload = {
        "name": name,
        "device_type": device_type_id,
        "role": role_id,
        "site": site_id,
        "status": "active",
    }

    if custom_fields is not None:
        payload["custom_fields"] = custom_fields

    if existing:
        api(
            "PATCH",
            f"dcim/devices/{existing['id']}/",
            json=payload,
        )

        print(
            f"[NetBox] {name}: device already exists; updated"
        )

        return api(
            "GET",
            f"dcim/devices/{existing['id']}/",
        )

    created = api(
        "POST",
        "dcim/devices/",
        json=payload,
    )

    print(
        f"[NetBox] {name}: device created"
    )

    return created


# ============================================================
# Interface
# ============================================================

def ensure_interface(
    device,
    name,
    interface_type,
):
    existing = maybe_one(
        "dcim/interfaces/",
        device_id=device["id"],
        name=name,
    )

    payload = {
        "device": device["id"],
        "name": name,
        "type": interface_type,
        "enabled": True,
    }

    if existing:
        api(
            "PATCH",
            f"dcim/interfaces/{existing['id']}/",
            json=payload,
        )

        return api(
            "GET",
            f"dcim/interfaces/{existing['id']}/",
        )

    created = api(
        "POST",
        "dcim/interfaces/",
        json=payload,
    )

    print(
        f"[NetBox] {device['name']} {name}: "
        f"interface created"
    )

    return created


# ============================================================
# IP
# ============================================================

def ensure_ip(
    address,
    interface,
    description,
):
    existing = maybe_one(
        "ipam/ip-addresses/",
        address=address,
    )

    payload = {
        "address": address,
        "status": "active",
        "assigned_object_type": "dcim.interface",
        "assigned_object_id": interface["id"],
        "description": description,
    }

    if existing:
        api(
            "PATCH",
            f"ipam/ip-addresses/{existing['id']}/",
            json=payload,
        )

        return api(
            "GET",
            f"ipam/ip-addresses/{existing['id']}/",
        )

    created = api(
        "POST",
        "ipam/ip-addresses/",
        json=payload,
    )

    print(
        f"[NetBox] {address}: IP created"
    )

    return created


def set_primary_ips(
    device,
    ip4_id,
    ip6_id,
):
    api(
        "PATCH",
        f"dcim/devices/{device['id']}/",
        json={
            "primary_ip4": ip4_id,
            "primary_ip6": ip6_id,
        },
    )

    print(
        f"[NetBox] {device['name']}: "
        f"primary IPv4/IPv6 set"
    )


# ============================================================
# Register ZTP network devices
# ============================================================

def register_network_devices(
    site,
    device_types,
    roles,
):
    for name, spec in DEVICES.items():

        custom_fields = {
            "credential_profile": "lab-network",
            "management_username": "admin",
            "secret_ref": spec["secret_ref"],
        }

        device = ensure_device(
            name,
            spec,
            site["id"],
            device_types[spec["device_type"]],
            roles[spec["role"]],
            custom_fields=custom_fields,
        )

        ip4_id = None
        ip6_id = None

        for if_name, if_spec in spec["interfaces"].items():

            interface = ensure_interface(
                device,
                if_name,
                if_spec["type"],
            )

            for address in if_spec["ips"]:

                family = 6 if ":" in address else 4

                if if_name in (
                    "Management0",
                    "Ma0",
                ):
                    description = (
                        f"{name} Management IPv{family}"
                    )
                else:
                    description = (
                        f"{name} {if_name}"
                    )

                ip = ensure_ip(
                    address,
                    interface,
                    description,
                )

                if address == spec["primary4"]:
                    ip4_id = ip["id"]

                if address == spec["primary6"]:
                    ip6_id = ip["id"]

        if not ip4_id:
            raise RuntimeError(
                f"{name}: primary IPv4 was not found"
            )

        if not ip6_id:
            raise RuntimeError(
                f"{name}: primary IPv6 was not found"
            )

        set_primary_ips(
            device,
            ip4_id,
            ip6_id,
        )


# ============================================================
# Register Alpine hosts
# ============================================================

def register_hosts(
    site,
    device_types,
    roles,
):
    print(
        "[NetBox] Registering H5/H6/H7..."
    )

    for name, spec in HOSTS.items():

        device = ensure_device(
            name,
            spec,
            site["id"],
            device_types[spec["device_type"]],
            roles[spec["role"]],
        )

        ip4_id = None
        ip6_id = None

        for if_name, if_spec in spec["interfaces"].items():

            interface = ensure_interface(
                device,
                if_name,
                if_spec["type"],
            )

            for address in if_spec["ips"]:

                family = 6 if ":" in address else 4

                if if_name == "eth0":
                    description = (
                        f"{name} Management IPv{family}"
                    )
                else:
                    description = (
                        f"{name} {if_name}"
                    )

                ip = ensure_ip(
                    address,
                    interface,
                    description,
                )

                if address == spec["primary4"]:
                    ip4_id = ip["id"]

                if address == spec["primary6"]:
                    ip6_id = ip["id"]

        if not ip4_id:
            raise RuntimeError(
                f"{name}: primary IPv4 was not found"
            )

        if not ip6_id:
            raise RuntimeError(
                f"{name}: primary IPv6 was not found"
            )

        set_primary_ips(
            device,
            ip4_id,
            ip6_id,
        )


# ============================================================
# Day-2 existing interfaces
# ============================================================

def ensure_day2_interfaces():
    print(
        "[NetBox] Checking Day-2 interfaces "
        "on existing devices..."
    )

    for device_name, interfaces in DAY2_INTERFACES.items():

        device = one(
            "dcim/devices/",
            name=device_name,
        )

        for if_name, if_spec in interfaces.items():

            interface = ensure_interface(
                device,
                if_name,
                if_spec["type"],
            )

            for address in if_spec["ips"]:

                ensure_ip(
                    address,
                    interface,
                    f"{device_name} {if_name}",
                )

            print(
                f"[NetBox] {device_name} {if_name}: "
                f"Day-2 interface verified"
            )


# ============================================================
# Cable helpers
# ============================================================

def get_interface(
    device_name,
    interface_name,
):
    device = one(
        "dcim/devices/",
        name=device_name,
    )

    return one(
        "dcim/interfaces/",
        device_id=device["id"],
        name=interface_name,
    )


def ensure_cable(a, b):
    a_device, a_interface = a
    b_device, b_interface = b

    interface_a = get_interface(
        a_device,
        a_interface,
    )

    interface_b = get_interface(
        b_device,
        b_interface,
    )

    cable_a = interface_a.get("cable")
    cable_b = interface_b.get("cable")

    # Already connected by the same cable
    if cable_a and cable_b:

        if cable_a["id"] == cable_b["id"]:

            print(
                f"[NetBox] Cable exists: "
                f"{a_device} {a_interface} "
                f"<-> "
                f"{b_device} {b_interface}"
            )

            return

        raise RuntimeError(
            f"Cable conflict: "
            f"{a_device} {a_interface} and "
            f"{b_device} {b_interface} "
            f"are connected to different cables"
        )

    # One side is already occupied
    if cable_a:
        raise RuntimeError(
            f"Cable conflict: "
            f"{a_device} {a_interface} "
            f"is already connected to cable "
            f"{cable_a['id']}"
        )

    if cable_b:
        raise RuntimeError(
            f"Cable conflict: "
            f"{b_device} {b_interface} "
            f"is already connected to cable "
            f"{cable_b['id']}"
        )

    payload = {
        "a_terminations": [
            {
                "object_type": "dcim.interface",
                "object_id": interface_a["id"],
            }
        ],
        "b_terminations": [
            {
                "object_type": "dcim.interface",
                "object_id": interface_b["id"],
            }
        ],
        "status": "connected",
    }

    cable = api(
        "POST",
        "dcim/cables/",
        json=payload,
    )

    print(
        f"[NetBox] Cable created "
        f"(ID {cable['id']}): "
        f"{a_device} {a_interface} "
        f"<-> "
        f"{b_device} {b_interface}"
    )


# ============================================================
# Main
# ============================================================

def main():

    print(
        "[NetBox] Starting ZTP/Day-2 registration"
    )

    site = one(
        "dcim/sites/",
        name=SITE_NAME,
    )

    ceos_type = one(
        "dcim/device-types/",
        model=CEOS_DEVICE_TYPE,
    )

    host_type = one(
        "dcim/device-types/",
        model=HOST_DEVICE_TYPE,
    )

    device_types = {
        CEOS_DEVICE_TYPE: ceos_type["id"],
        HOST_DEVICE_TYPE: host_type["id"],
    }

    roles = {
        "Router": one(
            "dcim/device-roles/",
            name="Router",
        )["id"],

        "Switch": one(
            "dcim/device-roles/",
            name="Switch",
        )["id"],

        "Host": one(
            "dcim/device-roles/",
            name="Host",
        )["id"],
    }

    # 1. R6/R7/R8/S5
    register_network_devices(
        site,
        device_types,
        roles,
    )

    # 2. H5/H6/H7
    register_hosts(
        site,
        device_types,
        roles,
    )

    # 3. R2/R4 Day-2 interfaces
    ensure_day2_interfaces()

    # 4. Cables / Connections
    print(
        "[NetBox] Checking topology cables..."
    )

    for endpoint_a, endpoint_b in CABLES:

        ensure_cable(
            endpoint_a,
            endpoint_b,
        )

    print(
        "[NetBox] Registration completed successfully"
    )


if __name__ == "__main__":
    try:
        main()

    except Exception as exc:
        print(
            f"[NetBox] ERROR: {exc}",
            file=sys.stderr,
        )

        sys.exit(1)