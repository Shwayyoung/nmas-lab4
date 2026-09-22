#!/usr/bin/env python3

import json
import os
import sys
import ipaddress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, parse_qs, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NETBOX_URL = os.environ.get(
    "NETBOX_URL",
    "http://127.0.0.1:8000",
).rstrip("/")

NETBOX_TOKEN = os.environ.get("NETBOX_TOKEN")

LISTEN_HOST = os.environ.get("LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8088"))

if not NETBOX_TOKEN:
    print("ERROR: NETBOX_TOKEN is not set", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# NetBox API helper
# ---------------------------------------------------------------------------

def netbox_get(path: str, params: dict | None = None) -> dict:
    """Perform a GET request against the NetBox REST API."""
    url = f"{NETBOX_URL}{path}"

    if params:
        query = "&".join(
            f"{quote(str(k))}={quote(str(v))}"
            for k, v in params.items()
        )
        url = f"{url}?{query}"

    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {NETBOX_TOKEN}",
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=10) as response:
            if response.status != 200:
                raise RuntimeError(
                    f"NetBox returned HTTP {response.status}"
                )

            body = response.read().decode("utf-8")
            return json.loads(body)

    except HTTPError as exc:
        raise RuntimeError(
            f"NetBox HTTP error {exc.code}: {exc.reason}"
        ) from exc

    except URLError as exc:
        raise RuntimeError(
            f"Cannot reach NetBox: {exc.reason}"
        ) from exc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_ip(value: str) -> str:
    """
    Normalize an IPv4/IPv6 address or a CIDR string to its host address.

    Examples:
        172.20.20.14       -> 172.20.20.14
        172.20.20.14/24    -> 172.20.20.14
    """
    value = value.strip()

    try:
        if "/" in value:
            return str(ipaddress.ip_interface(value).ip)

        return str(ipaddress.ip_address(value))

    except ValueError:
        raise ValueError(f"Invalid IP address: {value}")


# ---------------------------------------------------------------------------
# Device lookup
# ---------------------------------------------------------------------------

def get_device_by_name(device_name: str) -> dict:
    """Find a NetBox device by exact name."""
    data = netbox_get(
        "/api/dcim/devices/",
        {
            "name": device_name,
            "limit": 100,
        },
    )

    results = data.get("results", [])

    if not results:
        raise ValueError(
            f"Device '{device_name}' not found in NetBox"
        )

    if len(results) > 1:
        raise ValueError(
            f"Multiple devices named '{device_name}' found"
        )

    return results[0]


def get_device_by_mgmt_ip(mgmt_ip: str) -> dict:
    """
    Find the NetBox device owning the supplied management IP.

    Primary lookup:
        NetBox IP address API.

    Fallback:
        NetBox device primary_ip4 / primary_ip6.
    """

    normalized = normalize_ip(mgmt_ip)

    # ---------------------------------------------------------------
    # First try IPAM.
    #
    # q= is intentionally used here because it lets NetBox return
    # matching IP objects without requiring us to know whether the
    # stored address contains /24, /64, etc.
    # ---------------------------------------------------------------

    try:
        data = netbox_get(
            "/api/ipam/ip-addresses/",
            {
                "q": normalized,
                "limit": 1000,
            },
        )

        for ip_obj in data.get("results", []):
            address = ip_obj.get("address", "")

            try:
                ip_host = normalize_ip(address)
            except ValueError:
                continue

            if ip_host != normalized:
                continue

            assigned_object = ip_obj.get("assigned_object") or {}

            device = assigned_object.get("device") or {}

            if device.get("name"):
                return get_device_by_name(device["name"])

    except RuntimeError:
        # Continue to primary IP fallback.
        pass

    # ---------------------------------------------------------------
    # Fallback: inspect device primary IP fields.
    # ---------------------------------------------------------------

    data = netbox_get(
        "/api/dcim/devices/",
        {
            "limit": 1000,
        },
    )

    matches = []

    for device in data.get("results", []):
        for field_name in ("primary_ip4", "primary_ip6"):
            ip_obj = device.get(field_name)

            if not ip_obj:
                continue

            address = ip_obj.get("address", "")

            try:
                ip_host = normalize_ip(address)
            except ValueError:
                continue

            if ip_host == normalized:
                matches.append(device)
                break

    if not matches:
        raise ValueError(
            f"No NetBox device found for management IP '{mgmt_ip}'"
        )

    if len(matches) > 1:
        names = ", ".join(
            str(device.get("name"))
            for device in matches
        )

        raise ValueError(
            f"Multiple NetBox devices found for "
            f"management IP '{mgmt_ip}': {names}"
        )

    return matches[0]


# ---------------------------------------------------------------------------
# Interface inventory
# ---------------------------------------------------------------------------

def get_connected_interfaces(device_name: str) -> list[dict]:
    """
    Return only interfaces that have a NetBox cable.

    This intentionally excludes interfaces such as:
        ethernet-1/1.0
        lo0.0
        mgmt0

    when those interfaces do not have a cable.
    """

    device = get_device_by_name(device_name)
    device_id = device["id"]

    data = netbox_get(
        "/api/dcim/interfaces/",
        {
            "device_id": device_id,
            "limit": 1000,
        },
    )

    interfaces = []

    for interface in data.get("results", []):

        # Only physically connected interfaces.
        if not interface.get("cable"):
            continue

        peer_device = None
        peer_interface = None

        link_peers = interface.get("link_peers") or []

        if link_peers:
            peer = link_peers[0]

            peer_interface = peer.get("name")

            peer_device_data = peer.get("device") or {}
            peer_device = peer_device_data.get("name")

        interface_type = interface.get("type") or {}

        interfaces.append(
            {
                "name": interface.get("name"),
                "enabled": interface.get("enabled"),
                "type": interface_type.get("value"),
                "cable_id": interface["cable"]["id"],
                "cable_end": interface.get("cable_end"),
                "peer_device": peer_device,
                "peer_interface": peer_interface,
            }
        )

    interfaces.sort(
        key=lambda item: item.get("name") or ""
    )

    return interfaces


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):

    def send_json(
        self,
        payload: object,
        status: int = 200,
    ) -> None:

        body = json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        ).encode("utf-8")

        self.send_response(status)

        self.send_header(
            "Access-Control-Allow-Origin",
            "*",
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, OPTIONS",
        )

        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type",
        )

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )

        self.send_header(
            "Content-Length",
            str(len(body)),
        )

        self.end_headers()

        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)

        self.send_header(
            "Access-Control-Allow-Origin",
            "*",
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, OPTIONS",
        )

        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type",
        )

        self.end_headers()

    def do_GET(self) -> None:

        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # -----------------------------------------------------------
        # Health check
        # -----------------------------------------------------------

        if path == "/health":

            self.send_json(
                {
                    "status": "ok",
                    "service": "netbox-bridge",
                }
            )

            return

        # -----------------------------------------------------------
        # /api/devices/<device>/interfaces
        # -----------------------------------------------------------

        parts = path.strip("/").split("/")

        if (
            len(parts) == 4
            and parts[0] == "api"
            and parts[1] == "devices"
            and parts[3] == "interfaces"
        ):

            device_name = parts[2]

            try:
                interfaces = get_connected_interfaces(
                    device_name
                )

                self.send_json(
                    {
                        "device": device_name,
                        "count": len(interfaces),
                        "interfaces": interfaces,
                    }
                )

            except ValueError as exc:

                self.send_json(
                    {
                        "error": str(exc),
                    },
                    status=404,
                )

            except RuntimeError as exc:

                self.send_json(
                    {
                        "error": str(exc),
                    },
                    status=502,
                )

            except Exception as exc:

                self.send_json(
                    {
                        "error": f"Internal error: {exc}",
                    },
                    status=500,
                )

            return

        # -----------------------------------------------------------
        # /api/interfaces?device=R3
        #
        # Existing endpoint.
        # -----------------------------------------------------------

        if path == "/api/interfaces":

            device_values = query.get("device", [])

            mgmt_values = query.get("mgmt_ip", [])

            # -------------------------------------------------------
            # Device can be specified directly:
            #
            # ?device=R3
            # -------------------------------------------------------

            if device_values:

                device_name = device_values[0]

            # -------------------------------------------------------
            # Or by management IP:
            #
            # ?mgmt_ip=172.20.20.14
            # -------------------------------------------------------

            elif mgmt_values:

                mgmt_ip = mgmt_values[0]

                try:
                    device = get_device_by_mgmt_ip(
                        mgmt_ip
                    )

                    device_name = device["name"]

                except ValueError as exc:

                    self.send_json(
                        {
                            "error": str(exc),
                        },
                        status=404,
                    )

                    return

            else:

                self.send_json(
                    {
                        "error": (
                            "Missing required query parameter: "
                            "device or mgmt_ip"
                        ),
                    },
                    status=400,
                )

                return

            try:

                interfaces = get_connected_interfaces(
                    device_name
                )

                names = [
                    item["name"]
                    for item in interfaces
                    if item.get("name")
                ]

                # This is intentionally a plain JSON array because
                # Grafana Infinity + JQ can consume it directly.
                self.send_json(names)

            except ValueError as exc:

                self.send_json(
                    {
                        "error": str(exc),
                    },
                    status=404,
                )

            except RuntimeError as exc:

                self.send_json(
                    {
                        "error": str(exc),
                    },
                    status=502,
                )

            except Exception as exc:

                self.send_json(
                    {
                        "error": f"Internal error: {exc}",
                    },
                    status=500,
                )

            return

        # -----------------------------------------------------------
        # Unknown endpoint
        # -----------------------------------------------------------

        self.send_json(
            {
                "error": "Not found",
                "path": path,
            },
            status=404,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:

    server = ThreadingHTTPServer(
        (LISTEN_HOST, LISTEN_PORT),
        Handler,
    )

    print(
        "NetBox bridge listening on "
        f"http://{LISTEN_HOST}:{LISTEN_PORT}"
    )

    print(
        f"NetBox URL: {NETBOX_URL}"
    )

    try:

        server.serve_forever()

    except KeyboardInterrupt:

        print("\nStopping server...")

    finally:

        server.server_close()


if __name__ == "__main__":
    main()