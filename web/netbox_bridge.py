#!/usr/bin/env python3

import json
import os
import sys
import ipaddress
import networkx as nx
import csv
import io
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

INFLUX_TOKEN = os.environ.get("INFLUX_TOKEN")

#helper
def influx_query(flux: str) -> str:
    """
    Execute a Flux query against InfluxDB 2.x.
    Return annotated CSV text.
    """

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
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Accept": "application/csv",
            "Content-Type": "application/vnd.flux",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=15) as response:

            if response.status != 200:
                raise RuntimeError(
                    f"InfluxDB returned HTTP "
                    f"{response.status}"
                )

            return response.read().decode("utf-8")

    except HTTPError as exc:

        body = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        raise RuntimeError(
            f"InfluxDB HTTP error "
            f"{exc.code}: {body}"
        ) from exc

    except URLError as exc:

        raise RuntimeError(
            f"Cannot reach InfluxDB: "
            f"{exc.reason}"
        ) from exc

#helper
def build_interface_state_map(
    connected: dict | None = None,
) -> dict:
    """
    Build lookup table:

    {
        ("172.20.20.14", "ethernet-1/1"): {
            "state": "up",
            "time": "..."
        }
    }
    """

    states = get_latest_interface_states(
        connected=connected
    )

    state_map = {}

    for item in states:

        device_ip = item.get("device_ip")
        interface = item.get("interface")

        if not device_ip or not interface:
            continue

        state_map[(device_ip, interface)] = {
            "state": item.get("state"),
            "time": item.get("time"),
        }

    return state_map

#
def normalize_oper_state(state: str | None) -> str:
    """
    Normalize vendor-specific interface states.
    """

    if state is None:
        return "unknown"

    value = str(state).strip().lower()

    if value in ("up", "1", "true"):
        return "up"

    if value in ("down", "2", "false"):
        return "down"

    return "unknown"

def get_latest_interface_states(
    connected: dict | None = None,
) -> list[dict]:
    """
    Return latest interface states only for interfaces
    that are physically connected in NetBox.

    Optimized:
    - cEOS and SR Linux are queried separately.
    - last() is applied before map()/group().
    - This allows InfluxDB to retrieve only the latest
      point from each interface series instead of
      transforming the full time range first.
    """

    flux = f'''
ceos =
  from(bucket: "{INFLUX_BUCKET}")
    |> range(start: -30d)
    |> filter(fn: (r) =>
         r._measurement =~ /^(r|s)[0-9]+_interface_state$/
       )
    |> filter(fn: (r) =>
         r._field == "oper_status"
       )
    |> last()
    |> map(fn: (r) => ({{
         _time: r._time,
         _measurement: r._measurement,
         _value: r._value,

         device_ip:
           if exists r.agent_host then
             r.agent_host
           else
             r.source,

         interface: r.interface_name
       }}))

srl =
  from(bucket: "{INFLUX_BUCKET}")
    |> range(start: -30d)
    |> filter(fn: (r) =>
         r._measurement =~ /^r[0-9]+_interface_state$/
       )
    |> filter(fn: (r) =>
         r._field == "oper_state"
       )
    |> last()
    |> map(fn: (r) => ({{
         _time: r._time,
         _measurement: r._measurement,
         _value: r._value,
         device_ip: r.source,
         interface:
           r["srl_nokia_interfaces:interface_name"]
       }}))

union(tables: [ceos, srl])
  |> group()
  |> keep(columns: [
       "_time",
       "_measurement",
       "_value",
       "device_ip",
       "interface"
     ])
  |> sort(columns: ["device_ip", "interface"])
'''

    csv_text = influx_query(flux)

    if connected is None:
        snapshot = get_netbox_topology_snapshot()
        connected = snapshot["connected"]

    rows = []

    reader = csv.DictReader(
        line
        for line in io.StringIO(csv_text)
        if not line.startswith("#")
        and line.strip()
    )

    for row in reader:

        device_ip = row.get("device_ip")
        interface_name = row.get("interface")

        if not device_ip or not interface_name:
            continue

        try:
            device_ip = normalize_ip(device_ip)
        except ValueError:
            continue

        # Only interfaces physically connected in NetBox.
        if device_ip not in connected:
            continue

        if interface_name not in connected[device_ip]:
            continue

        rows.append({
            "device_ip": device_ip,
            "interface": interface_name,
            "state": row.get("_value"),
            "measurement": row.get("_measurement"),
            "time": row.get("_time"),
        })

    return rows

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
# Topology
# ---------------------------------------------------------------------------

def get_all_devices() -> list[dict]:
    """Return all devices from NetBox."""

    data = netbox_get(
        "/api/dcim/devices/",
        {
            "limit": 1000,
        },
    )

    return data.get("results", [])


def get_grafana_devices() -> list[dict]:
    """
    Return active Router/Switch devices with a primary IPv4
    in Grafana Infinity variable format:

        [
            {"text": "R1", "value": "172.20.20.6"},
            ...
        ]
    """

    devices = get_all_devices()
    result = []

    for device in devices:
        name = device.get("name")

        if not name:
            continue

        status = device.get("status") or {}
        status_value = (
            status.get("value")
            or status.get("label")
            or status.get("display")
            or ""
        )

        if str(status_value).strip().lower() != "active":
            continue

        role = device.get("role") or {}
        role_slug = (
            role.get("slug")
            or ""
        ).strip().lower()

        if role_slug not in (
            "router",
            "switch",
            "l3switch",
        ):
            continue

        primary_ip4 = device.get("primary_ip4")
        if not primary_ip4:
            continue

        address = primary_ip4.get("address")
        if not address:
            continue

        try:
            mgmt_ip = normalize_ip(address)
        except ValueError:
            continue

        result.append({
            "text": name,
            "value": mgmt_ip,
        })

    def sort_key(item):
        name = item["text"]
        prefix = name[:1].upper()

        try:
            number = int(name[1:])
        except (TypeError, ValueError):
            number = 10**9

        prefix_order = {
            "R": 0,
            "S": 1,
        }.get(prefix, 2)

        return (prefix_order, number, name)

    result.sort(key=sort_key)

    return result

def get_netbox_topology_snapshot() -> dict:
    """
    Read devices and interfaces from NetBox once.

    Returns:
        devices:
            Raw NetBox device objects.

        device_mgmt:
            device name -> management IP.

        connected:
            management IP -> set of cabled interface names.

        interfaces:
            Normalized physical interfaces with cables.
    """

    devices = get_all_devices()

    device_mgmt = {}

    for device in devices:

        device_name = device.get("name")

        if not device_name:
            continue

        primary_ip = (
            device.get("primary_ip4")
            or device.get("primary_ip6")
        )

        mgmt_ip = None

        if primary_ip:

            address = primary_ip.get("address")

            if address:

                try:
                    mgmt_ip = normalize_ip(address)

                except ValueError:
                    mgmt_ip = address

        device_mgmt[device_name] = mgmt_ip


    # One request for ALL interfaces.
    data = netbox_get(
        "/api/dcim/interfaces/",
        {
            "limit": 1000,
        },
    )

    connected = {}
    interfaces = []

    for interface in data.get("results", []):

        cable = interface.get("cable")

        if not cable:
            continue

        device_data = interface.get("device") or {}
        device_name = device_data.get("name")

        interface_name = interface.get("name")

        if not device_name or not interface_name:
            continue

        peer_device = None
        peer_interface = None

        link_peers = interface.get("link_peers") or []

        if link_peers:

            peer = link_peers[0]

            peer_interface = peer.get("name")

            peer_device_data = peer.get("device") or {}
            peer_device = peer_device_data.get("name")

        interfaces.append({
            "device": device_name,
            "name": interface_name,
            "cable_id": cable.get("id"),
            "peer_device": peer_device,
            "peer_interface": peer_interface,
        })

        mgmt_ip = device_mgmt.get(device_name)

        if mgmt_ip:

            connected.setdefault(
                mgmt_ip,
                set(),
            ).add(interface_name)

    return {
        "devices": devices,
        "device_mgmt": device_mgmt,
        "connected": connected,
        "interfaces": interfaces,
    }

def build_topology() -> dict:
    """
    Build NetworkX topology from one NetBox snapshot
    plus one InfluxDB interface-state query.
    """

    graph = nx.Graph()

    # ---------------------------------------------------------------
    # Read NetBox inventory only once
    # ---------------------------------------------------------------

    snapshot = get_netbox_topology_snapshot()

    devices = snapshot["devices"]
    device_mgmt = snapshot["device_mgmt"]
    connected = snapshot["connected"]
    interfaces = snapshot["interfaces"]

    # ---------------------------------------------------------------
    # Read telemetry once
    # ---------------------------------------------------------------

    state_map = build_interface_state_map(
        connected=connected
    )

    # ---------------------------------------------------------------
    # Nodes
    # ---------------------------------------------------------------

    for device in devices:

        device_name = device.get("name")

        if not device_name:
            continue

        mgmt_ip = device_mgmt.get(device_name)

        role_data = device.get("role") or {}

        device_type = (
            device.get("device_type")
            or {}
        )

        manufacturer_data = (
            device_type.get("manufacturer")
            or {}
        )

        graph.add_node(
            device_name,

            label=device_name,

            mgmt_ip=mgmt_ip,

            role=(
                role_data.get("name")
                or role_data.get("display")
            ),

            manufacturer=(
                manufacturer_data.get("name")
            ),

            model=device_type.get("model"),
        )

    # ---------------------------------------------------------------
    # Edges
    # ---------------------------------------------------------------

    seen_cables = set()

    for interface in interfaces:

        device_name = interface.get("device")
        local_interface = interface.get("name")

        peer_device = interface.get(
            "peer_device"
        )

        peer_interface = interface.get(
            "peer_interface"
        )

        cable_id = interface.get("cable_id")

        if not device_name:
            continue

        if not peer_device:
            continue

        if not peer_interface:
            continue

        if cable_id in seen_cables:
            continue

        seen_cables.add(cable_id)

        if peer_device not in graph:

            graph.add_node(
                peer_device,
                label=peer_device,
            )

        source_ip = device_mgmt.get(
            device_name
        )

        target_ip = device_mgmt.get(
            peer_device
        )

        source_record = state_map.get(
            (
                source_ip,
                local_interface,
            ),
            {},
        )

        target_record = state_map.get(
            (
                target_ip,
                peer_interface,
            ),
            {},
        )

        source_state = normalize_oper_state(
            source_record.get("state")
        )

        target_state = normalize_oper_state(
            target_record.get("state")
        )

        if (
            source_state == "up"
            and target_state == "up"
        ):
            link_status = "up"

        elif (
            source_state == "down"
            or target_state == "down"
        ):
            link_status = "down"

        else:
            link_status = "unknown"

        graph.add_edge(
            device_name,
            peer_device,

            id=f"cable-{cable_id}",
            cable_id=cable_id,

            source_interface=local_interface,
            target_interface=peer_interface,

            source_state=source_state,
            target_state=target_state,

            source_time=source_record.get(
                "time"
            ),

            target_time=target_record.get(
                "time"
            ),

            status=link_status,

            label=(
                f"{local_interface} <-> "
                f"{peer_interface}"
            ),
        )

    # ---------------------------------------------------------------
    # JSON output
    # ---------------------------------------------------------------

    nodes = []

    for node_name, attrs in graph.nodes(
        data=True
    ):

        nodes.append({
            "id": node_name,

            "label": attrs.get(
                "label",
                node_name,
            ),

            "mgmt_ip": attrs.get(
                "mgmt_ip"
            ),

            "role": attrs.get(
                "role"
            ),

            "manufacturer": attrs.get(
                "manufacturer"
            ),

            "model": attrs.get(
                "model"
            ),
        })

    edges = []

    for source, target, attrs in graph.edges(
        data=True
    ):

        edges.append({
            "id": attrs.get(
                "id",
                f"{source}-{target}",
            ),

            "source": source,
            "target": target,

            "source_interface": attrs.get(
                "source_interface"
            ),

            "target_interface": attrs.get(
                "target_interface"
            ),

            "source_state": attrs.get(
                "source_state",
                "unknown",
            ),

            "target_state": attrs.get(
                "target_state",
                "unknown",
            ),

            "source_time": attrs.get(
                "source_time"
            ),

            "target_time": attrs.get(
                "target_time"
            ),

            "status": attrs.get(
                "status",
                "unknown",
            ),

            "cable_id": attrs.get(
                "cable_id"
            ),

            "label": attrs.get(
                "label"
            ),
        })

    return {
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
    }

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
        # /api/devices
        #
        # Grafana Infinity variable endpoint.
        # Returns active Router/Switch devices as:
        #
        # [
        #   {"text": "R1", "value": "172.20.20.6"},
        #   ...
        # ]
        # -----------------------------------------------------------

        if path == "/api/devices":

            try:

                devices = get_grafana_devices()

                self.send_json(devices)

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
        # /api/topology
        # -----------------------------------------------------------

        if path == "/api/topology":

            try:

                topology = build_topology()

                self.send_json(topology)

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
        # /api/interface-status
        # -----------------------------------------------------------

        if path == "/api/interface-status":

            try:

                states = get_latest_interface_states()

                self.send_json(
                    {
                        "count": len(states),
                        "interfaces": states,
                    }
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