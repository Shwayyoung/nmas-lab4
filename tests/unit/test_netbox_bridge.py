import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest


BRIDGE_FILE = (
    Path(__file__).resolve().parents[2]
    / "web" / "netbox_bridge.py"
)


@pytest.fixture
def bridge(monkeypatch):
    monkeypatch.setenv("NETBOX_TOKEN", "test-token")

    spec = importlib.util.spec_from_file_location(
        "netbox_bridge_under_test", BRIDGE_FILE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("value,expected", [
    ("172.20.20.6", "172.20.20.6"),
    ("172.20.20.6/24", "172.20.20.6"),
    ("2001:db8::1/64", "2001:db8::1"),
    (" 10.0.0.1 ", "10.0.0.1"),
])
def test_normalize_ip_valid(bridge, value, expected):
    assert bridge.normalize_ip(value) == expected


@pytest.mark.parametrize("value", [
    "999.999.999.999",
    "not-an-ip",
    "192.168.1.1/33",
])
def test_normalize_ip_invalid(bridge, value):
    with pytest.raises(ValueError, match="Invalid IP address"):
        bridge.normalize_ip(value)


@pytest.mark.parametrize("value,expected", [
    ("up", "up"),
    ("1", "up"),
    ("TRUE", "up"),
    ("down", "down"),
    ("2", "down"),
    ("false", "down"),
    ("something-else", "unknown"),
    (None, "unknown"),
])
def test_normalize_oper_state(bridge, value, expected):
    assert bridge.normalize_oper_state(value) == expected


def test_netbox_get_success(bridge):
    expected = {"results": [{"name": "R1"}]}

    response = MagicMock()
    response.status = 200
    response.read.return_value = json.dumps(expected).encode()

    context = MagicMock()
    context.__enter__.return_value = response

    with patch.object(
        bridge, "urlopen", return_value=context
    ) as mock_open:
        result = bridge.netbox_get(
            "/api/dcim/devices/",
            {"name": "R1", "limit": 100},
        )

    assert result == expected

    request = mock_open.call_args.args[0]
    assert request.get_method() == "GET"
    assert "name=R1" in request.full_url
    assert "limit=100" in request.full_url
    assert request.get_header("Authorization") == "Bearer test-token"
    assert mock_open.call_args.kwargs["timeout"] == 10


def test_netbox_get_network_error(bridge):
    with patch.object(
        bridge, "urlopen",
        side_effect=URLError("Connection refused"),
    ):
        with pytest.raises(RuntimeError, match="Cannot reach NetBox"):
            bridge.netbox_get("/api/dcim/devices/")


def test_get_device_by_name_success(bridge):
    device = {"name": "R1", "id": 1}

    with patch.object(
        bridge, "netbox_get",
        return_value={"results": [device]},
    ) as mock_get:
        result = bridge.get_device_by_name("R1")

    assert result == device
    mock_get.assert_called_once_with(
        "/api/dcim/devices/",
        {"name": "R1", "limit": 100},
    )


@pytest.mark.parametrize("results,error", [
    ([], "not found"),
    ([{"name": "R1"}, {"name": "R1"}], "Multiple devices"),
])
def test_get_device_by_name_invalid(bridge, results, error):
    with patch.object(
        bridge, "netbox_get",
        return_value={"results": results},
    ):
        with pytest.raises(ValueError, match=error):
            bridge.get_device_by_name("R1")

def test_influx_missing_token(bridge):
    with patch.object(bridge, "INFLUX_TOKEN", None):
        with pytest.raises(RuntimeError, match="INFLUX_TOKEN"):
            bridge.influx_query("test-flux")


def test_influx_success(bridge):
    response = MagicMock()
    response.status = 200
    response.read.return_value = b"_time,_value\n2026-10-19,1\n"

    context = MagicMock()
    context.__enter__.return_value = response

    with patch.object(bridge, "INFLUX_TOKEN", "test-token"), \
         patch.object(bridge, "urlopen", return_value=context) as mock_open:

        result = bridge.influx_query("test-flux")

    assert "_time,_value" in result
    request = mock_open.call_args.args[0]
    assert request.get_method() == "POST"
    assert request.data == b"test-flux"
    assert request.get_header("Authorization") == "Token test-token"
    assert mock_open.call_args.kwargs["timeout"] == 15


def test_influx_network_error(bridge):
    with patch.object(bridge, "INFLUX_TOKEN", "test-token"), \
         patch.object(
             bridge, "urlopen",
             side_effect=URLError("Connection refused")
         ):
        with pytest.raises(RuntimeError, match="Cannot reach InfluxDB"):
            bridge.influx_query("test-flux")


def test_latest_interface_states(bridge):
    csv_data = (
        "#group,false,false,false,false,false,false\n"
        "_time,_measurement,_value,device_ip,interface\n"
        "2026-10-19T12:00:00Z,r1_interface_state,1,172.20.20.6,Ethernet1\n"
        "2026-10-19T12:00:00Z,r1_interface_state,2,172.20.20.6,Ethernet2\n"
        "2026-10-19T12:00:00Z,r3_interface_state,1,invalid-ip,ethernet-1/1\n"
        "2026-10-19T12:00:00Z,r2_interface_state,1,172.20.20.4,Ethernet1\n"
    )

    connected = {
        "172.20.20.6": {"Ethernet1"},
        "172.20.20.4": {"Ethernet2"},
    }

    with patch.object(
        bridge, "influx_query", return_value=csv_data
    ) as mock_query:
        result = bridge.get_latest_interface_states(
            connected=connected
        )

    assert len(result) == 1
    assert result[0]["device_ip"] == "172.20.20.6"
    assert result[0]["interface"] == "Ethernet1"
    assert result[0]["state"] == "1"
    assert "interface_state" in mock_query.call_args.args[0]


def test_latest_interface_states_empty(bridge):
    with patch.object(
        bridge, "influx_query",
        return_value="_time,_value,device_ip,interface\n"
    ):
        result = bridge.get_latest_interface_states(
            connected={}
        )

    assert result == []


def test_build_interface_state_map(bridge):
    states = [
        {
            "device_ip": "172.20.20.6",
            "interface": "Ethernet1",
            "state": "up",
            "time": "2026-10-19T12:00:00Z",
        },
        {
            "device_ip": "172.20.20.4",
            "interface": None,
            "state": "down",
        },
    ]

    with patch.object(
        bridge, "get_latest_interface_states",
        return_value=states
    ) as mock_states:
        result = bridge.build_interface_state_map(
            connected={"172.20.20.6": {"Ethernet1"}}
        )

    assert result == {
        ("172.20.20.6", "Ethernet1"): {
            "state": "up",
            "time": "2026-10-19T12:00:00Z",
        }
    }
    mock_states.assert_called_once_with(
        connected={"172.20.20.6": {"Ethernet1"}}
    )

def test_device_by_mgmt_ip_ipam(bridge):
    ipam = {"results": [{
        "address": "172.20.20.6/24",
        "assigned_object": {
            "device": {"name": "R1"}
        },
    }]}

    with patch.object(
        bridge, "netbox_get", return_value=ipam
    ) as mock_get, patch.object(
        bridge, "get_device_by_name",
        return_value={"id": 1, "name": "R1"}
    ) as mock_device:
        result = bridge.get_device_by_mgmt_ip("172.20.20.6")

    assert result["name"] == "R1"
    mock_device.assert_called_once_with("R1")
    mock_get.assert_called_once_with(
        "/api/ipam/ip-addresses/",
        {"q": "172.20.20.6", "limit": 1000},
    )


def test_device_by_mgmt_ip_fallback(bridge):
    devices = {"results": [
        {"name": "R1", "primary_ip4": {
            "address": "172.20.20.6/24"
        }},
        {"name": "R2", "primary_ip4": {
            "address": "172.20.20.4/24"
        }},
    ]}

    with patch.object(
        bridge, "netbox_get",
        side_effect=[{"results": []}, devices]
    ):
        result = bridge.get_device_by_mgmt_ip("172.20.20.6")

    assert result["name"] == "R1"


def test_device_by_mgmt_ip_not_found(bridge):
    with patch.object(
        bridge, "netbox_get",
        return_value={"results": []}
    ):
        with pytest.raises(ValueError, match="No NetBox device found"):
            bridge.get_device_by_mgmt_ip("192.0.2.99")


def test_connected_interfaces(bridge):
    interfaces = {"results": [
        {
            "name": "Ethernet2",
            "enabled": True,
            "type": {"value": "1000base-t"},
            "cable": {"id": 12},
            "cable_end": "A",
            "link_peers": [{
                "name": "Ethernet1",
                "device": {"name": "R2"},
            }],
        },
        {
            "name": "Management0",
            "cable": None,
        },
        {
            "name": "Ethernet1",
            "enabled": False,
            "type": {"value": "1000base-t"},
            "cable": {"id": 11},
            "cable_end": "B",
            "link_peers": [],
        },
    ]}

    with patch.object(
        bridge, "get_device_by_name",
        return_value={"id": 1, "name": "R1"}
    ), patch.object(
        bridge, "netbox_get", return_value=interfaces
    ) as mock_get:
        result = bridge.get_connected_interfaces("R1")

    assert [x["name"] for x in result] == [
        "Ethernet1", "Ethernet2"
    ]
    assert result[0]["peer_device"] is None
    assert result[1]["peer_device"] == "R2"
    assert result[1]["peer_interface"] == "Ethernet1"
    mock_get.assert_called_once_with(
        "/api/dcim/interfaces/",
        {"device_id": 1, "limit": 1000},
    )


def test_connected_interfaces_empty(bridge):
    with patch.object(
        bridge, "get_device_by_name",
        return_value={"id": 1}
    ), patch.object(
        bridge, "netbox_get",
        return_value={"results": []}
    ):
        assert bridge.get_connected_interfaces("R1") == []


def test_get_all_devices(bridge):
    devices = [{"name": "R1"}, {"name": "R3"}]

    with patch.object(
        bridge, "netbox_get",
        return_value={"results": devices}
    ) as mock_get:
        assert bridge.get_all_devices() == devices

    mock_get.assert_called_once_with(
        "/api/dcim/devices/",
        {"limit": 1000},
    )

def test_topology_snapshot_success(bridge):
    devices = [
        {
            "name": "R1",
            "primary_ip4": {"address": "172.20.20.6/24"},
        },
        {
            "name": "R3",
            "primary_ip6": {"address": "2001:db8::3/64"},
        },
    ]

    interfaces = [
        {
            "device": {"name": "R1"},
            "name": "Ethernet1",
            "cable": {"id": 100},
            "link_peers": [{
                "device": {"name": "R3"},
                "name": "ethernet-1/1",
            }],
        },
        {
            "device": {"name": "R3"},
            "name": "ethernet-1/1",
            "cable": {"id": 100},
            "link_peers": [{
                "device": {"name": "R1"},
                "name": "Ethernet1",
            }],
        },
        {
            "device": {"name": "R1"},
            "name": "Management0",
            "cable": None,
        },
    ]

    with patch.object(
        bridge, "get_all_devices", return_value=devices
    ) as mock_devices, patch.object(
        bridge, "netbox_get",
        return_value={"results": interfaces},
    ) as mock_get:

        result = bridge.get_netbox_topology_snapshot()

    assert result["devices"] == devices
    assert result["device_mgmt"] == {
        "R1": "172.20.20.6",
        "R3": "2001:db8::3",
    }
    assert result["connected"] == {
        "172.20.20.6": {"Ethernet1"},
        "2001:db8::3": {"ethernet-1/1"},
    }
    assert len(result["interfaces"]) == 2

    r1_interface = result["interfaces"][0]
    assert r1_interface == {
        "device": "R1",
        "name": "Ethernet1",
        "cable_id": 100,
        "peer_device": "R3",
        "peer_interface": "ethernet-1/1",
    }

    mock_devices.assert_called_once()
    mock_get.assert_called_once_with(
        "/api/dcim/interfaces/", {"limit": 1000}
    )


def test_topology_snapshot_missing_fields(bridge):
    devices = [
        {"name": "R1", "primary_ip4": {
            "address": "invalid-ip"
        }},
        {"name": "R2"},
        {"primary_ip4": {"address": "192.0.2.1/24"}},
    ]

    interfaces = [
        {
            "device": {"name": "R1"},
            "name": "Ethernet1",
            "cable": {"id": 10},
            "link_peers": [],
        },
        {
            "device": {"name": "R2"},
            "name": "Ethernet2",
            "cable": {"id": 11},
            "link_peers": [],
        },
        {
            "device": {"name": "R1"},
            "cable": {"id": 12},
        },
        {
            "name": "Ethernet3",
            "cable": {"id": 13},
        },
    ]

    with patch.object(
        bridge, "get_all_devices", return_value=devices
    ), patch.object(
        bridge, "netbox_get",
        return_value={"results": interfaces}
    ):
        result = bridge.get_netbox_topology_snapshot()

    assert result["device_mgmt"] == {
        "R1": "invalid-ip",
        "R2": None,
    }
    assert result["connected"] == {
        "invalid-ip": {"Ethernet1"},
    }
    assert len(result["interfaces"]) == 2
    assert result["interfaces"][0]["peer_device"] is None
    assert result["interfaces"][0]["peer_interface"] is None


def test_topology_snapshot_empty(bridge):
    with patch.object(
        bridge, "get_all_devices", return_value=[]
    ), patch.object(
        bridge, "netbox_get", return_value={"results": []}
    ):
        result = bridge.get_netbox_topology_snapshot()

    assert result == {
        "devices": [],
        "device_mgmt": {},
        "connected": {},
        "interfaces": [],
    }

@pytest.fixture
def topology_data():
    devices = [
        {
            "name": "R1",
            "role": {"name": "router"},
            "device_type": {
                "manufacturer": {"name": "Arista"},
                "model": "cEOS",
            },
        },
        {
            "name": "R3",
            "role": {"name": "router"},
            "device_type": {
                "manufacturer": {"name": "Nokia"},
                "model": "SR Linux",
            },
        },
    ]

    return {
        "devices": devices,
        "device_mgmt": {
            "R1": "172.20.20.6",
            "R3": "172.20.20.14",
        },
        "connected": {
            "172.20.20.6": {"Ethernet1"},
            "172.20.20.14": {"ethernet-1/1"},
        },
        "interfaces": [
            {
                "device": "R1",
                "name": "Ethernet1",
                "peer_device": "R3",
                "peer_interface": "ethernet-1/1",
                "cable_id": 100,
            },
            {
                "device": "R3",
                "name": "ethernet-1/1",
                "peer_device": "R1",
                "peer_interface": "Ethernet1",
                "cable_id": 100,
            },
        ],
    }


@pytest.mark.parametrize("states,expected", [
    (("up", "up"), "up"),
    (("down", "up"), "down"),
    (("up", "down"), "down"),
    (("unknown", "up"), "unknown"),
    (("unknown", "unknown"), "unknown"),
])
def test_build_topology_link_status(
    bridge, topology_data, states, expected
):
    state_map = {
        ("172.20.20.6", "Ethernet1"): {
            "state": states[0],
            "time": "2026-10-19T12:00:00Z",
        },
        ("172.20.20.14", "ethernet-1/1"): {
            "state": states[1],
            "time": "2026-10-19T12:00:00Z",
        },
    }

    with patch.object(
        bridge, "get_netbox_topology_snapshot",
        return_value=topology_data
    ), patch.object(
        bridge, "build_interface_state_map",
        return_value=state_map
    ) as mock_states:
        result = bridge.build_topology()

    assert result["node_count"] == 2
    assert result["edge_count"] == 1

    nodes = {n["id"]: n for n in result["nodes"]}
    assert nodes["R1"]["manufacturer"] == "Arista"
    assert nodes["R1"]["model"] == "cEOS"
    assert nodes["R3"]["manufacturer"] == "Nokia"

    edge = result["edges"][0]
    assert edge["id"] == "cable-100"
    assert edge["status"] == expected
    assert edge["cable_id"] == 100
    assert {edge["source"], edge["target"]} == {"R1", "R3"}

    mock_states.assert_called_once_with(
        connected=topology_data["connected"]
    )


def test_build_topology_empty(bridge):
    snapshot = {
        "devices": [],
        "device_mgmt": {},
        "connected": {},
        "interfaces": [],
    }

    with patch.object(
        bridge, "get_netbox_topology_snapshot",
        return_value=snapshot
    ), patch.object(
        bridge, "build_interface_state_map",
        return_value={}
    ):
        result = bridge.build_topology()

    assert result == {
        "nodes": [],
        "edges": [],
        "node_count": 0,
        "edge_count": 0,
    }


def test_build_topology_unknown_peer(bridge, topology_data):
    topology_data["devices"] = topology_data["devices"][:1]
    topology_data["interfaces"] = topology_data["interfaces"][:1]

    with patch.object(
        bridge, "get_netbox_topology_snapshot",
        return_value=topology_data
    ), patch.object(
        bridge, "build_interface_state_map",
        return_value={}
    ):
        result = bridge.build_topology()

    assert result["node_count"] == 2
    assert result["edge_count"] == 1

    nodes = {n["id"]: n for n in result["nodes"]}
    assert "R3" in nodes
    assert nodes["R3"]["label"] == "R3"
    assert result["edges"][0]["status"] == "unknown"

@pytest.fixture
def handler(bridge):
    # Bypass HTTP server initialization.
    instance = bridge.Handler.__new__(bridge.Handler)
    instance.send_json = MagicMock()
    return instance


def test_handler_health(handler):
    handler.path = "/health"
    handler.do_GET()

    handler.send_json.assert_called_once_with({
        "status": "ok",
        "service": "netbox-bridge",
    })


def test_handler_device_interfaces(bridge, handler):
    handler.path = "/api/devices/R1/interfaces"
    interfaces = [{"name": "Ethernet1"}]

    with patch.object(
        bridge, "get_connected_interfaces",
        return_value=interfaces
    ) as mock_get:
        handler.do_GET()

    mock_get.assert_called_once_with("R1")
    handler.send_json.assert_called_once_with({
        "device": "R1",
        "count": 1,
        "interfaces": interfaces,
    })


def test_handler_device_not_found(bridge, handler):
    handler.path = "/api/devices/R99/interfaces"

    with patch.object(
        bridge, "get_connected_interfaces",
        side_effect=ValueError("Device not found")
    ):
        handler.do_GET()

    handler.send_json.assert_called_once_with(
        {"error": "Device not found"},
        status=404,
    )


@pytest.mark.parametrize("failure,status", [
    (RuntimeError("NetBox unavailable"), 502),
    (Exception("Unexpected error"), 500),
])
def test_handler_topology_error(bridge, handler, failure, status):
    handler.path = "/api/topology"

    with patch.object(
        bridge, "build_topology",
        side_effect=failure
    ):
        handler.do_GET()

    assert handler.send_json.call_args.kwargs["status"] == status


def test_handler_topology_success(bridge, handler):
    handler.path = "/api/topology"
    topology = {
        "nodes": [], "edges": [],
        "node_count": 0, "edge_count": 0,
    }

    with patch.object(
        bridge, "build_topology", return_value=topology
    ):
        handler.do_GET()

    handler.send_json.assert_called_once_with(topology)


def test_handler_interfaces_by_device(bridge, handler):
    handler.path = "/api/interfaces?device=R1"

    with patch.object(
        bridge, "get_connected_interfaces",
        return_value=[
            {"name": "Ethernet1"},
            {"name": "Ethernet2"},
            {"name": None},
        ]
    ):
        handler.do_GET()

    handler.send_json.assert_called_once_with(
        ["Ethernet1", "Ethernet2"]
    )


def test_handler_missing_query(handler):
    handler.path = "/api/interfaces"
    handler.do_GET()

    assert handler.send_json.call_args.kwargs["status"] == 400


def test_handler_interface_status(bridge, handler):
    handler.path = "/api/interface-status"
    states = [{"interface": "Ethernet1", "state": "up"}]

    with patch.object(
        bridge, "get_latest_interface_states",
        return_value=states
    ):
        handler.do_GET()

    handler.send_json.assert_called_once_with({
        "count": 1,
        "interfaces": states,
    })


def test_handler_unknown_endpoint(handler):
    handler.path = "/unknown"
    handler.do_GET()

    handler.send_json.assert_called_once_with(
        {"error": "Not found", "path": "/unknown"},
        status=404,
    )

def test_handler_interfaces_by_mgmt_ip(bridge, handler):
    handler.path = "/api/interfaces?mgmt_ip=172.20.20.6"

    with patch.object(
        bridge, "get_device_by_mgmt_ip",
        return_value={"name": "R1"}
    ) as lookup, patch.object(
        bridge, "get_connected_interfaces",
        return_value=[{"name": "Ethernet1"}]
    ) as interfaces:
        handler.do_GET()

    lookup.assert_called_once_with("172.20.20.6")
    interfaces.assert_called_once_with("R1")
    handler.send_json.assert_called_once_with(["Ethernet1"])


def test_handler_mgmt_ip_not_found(bridge, handler):
    handler.path = "/api/interfaces?mgmt_ip=192.0.2.99"

    with patch.object(
        bridge, "get_device_by_mgmt_ip",
        side_effect=ValueError("Device not found")
    ), patch.object(
        bridge, "get_connected_interfaces"
    ) as interfaces:
        handler.do_GET()

    interfaces.assert_not_called()
    handler.send_json.assert_called_once_with(
        {"error": "Device not found"}, status=404
    )


@pytest.mark.parametrize("path,function_name", [
    ("/api/interfaces?device=R1", "get_connected_interfaces"),
    ("/api/devices/R1/interfaces", "get_connected_interfaces"),
    ("/api/interface-status", "get_latest_interface_states"),
])
@pytest.mark.parametrize("error,status", [
    (RuntimeError("Service unavailable"), 502),
    (Exception("Unexpected failure"), 500),
])
def test_handler_service_errors(
    bridge, handler, path, function_name, error, status
):
    handler.path = path

    with patch.object(
        bridge, function_name, side_effect=error
    ):
        handler.do_GET()

    assert handler.send_json.call_args.kwargs["status"] == status
    assert "error" in handler.send_json.call_args.args[0]


def test_send_json_response(bridge):
    from io import BytesIO

    instance = bridge.Handler.__new__(bridge.Handler)
    instance.wfile = BytesIO()
    instance.send_response = MagicMock()
    instance.send_header = MagicMock()
    instance.end_headers = MagicMock()

    payload = {"device": "R1", "status": "up"}
    instance.send_json(payload, status=201)

    instance.send_response.assert_called_once_with(201)

    body = instance.wfile.getvalue()
    assert json.loads(body.decode("utf-8")) == payload

    headers = dict(
        call.args
        for call in instance.send_header.call_args_list
    )
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert int(headers["Content-Length"]) == len(body)
    instance.end_headers.assert_called_once()


def test_handler_options(bridge):
    instance = bridge.Handler.__new__(bridge.Handler)
    instance.send_response = MagicMock()
    instance.send_header = MagicMock()
    instance.end_headers = MagicMock()

    instance.do_OPTIONS()

    instance.send_response.assert_called_once_with(204)
    headers = dict(
        call.args
        for call in instance.send_header.call_args_list
    )
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert headers["Access-Control-Allow-Methods"] == "GET, OPTIONS"
    instance.end_headers.assert_called_once()