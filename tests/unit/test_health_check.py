import sys
import types
import pytest
import json

from unittest.mock import patch
from unittest.mock import MagicMock
from urllib.error import URLError

# Stub only the missing credentials dependency.
fake_credentials = types.ModuleType("credentials")
fake_credentials.get_password = lambda *args, **kwargs: "test-only"
sys.modules.setdefault("credentials", fake_credentials)

from automation import health_check


def test_health_check_module_import():
    assert hasattr(health_check, "check_ping")

def test_ping_success():
    with patch.object(health_check.subprocess, "run") as mock_run:
        mock_run.return_value.returncode = 0

        result = health_check.check_ping("192.0.2.1")

        assert result["status"] == "pass"
        assert result["reachable"] is True
        mock_run.assert_called_once()


def test_ping_failure():
    with patch.object(health_check.subprocess, "run") as mock_run:
        mock_run.return_value.returncode = 1

        result = health_check.check_ping("192.0.2.1")

        assert result["status"] == "fail"
        assert result["reachable"] is False
        mock_run.assert_called_once()

@pytest.mark.parametrize(
    "input_ip, expected",
    [
        ("192.168.1.1", "192.168.1.1"),
        ("10.0.0.1/24", "10.0.0.1"),
        ("172.20.20.6/24", "172.20.20.6"),
        ("2001:db8::1", "2001:db8::1"),
        ("2001:db8::1/64", "2001:db8::1"),
        ("2001:0db8:0:0:0:0:0:1", "2001:db8::1"),
    ],
)
def test_normalize_ip_valid(input_ip, expected):
    result = health_check.normalize_ip(input_ip)
    assert result == expected


@pytest.mark.parametrize(
    "invalid_ip",
    [
        "999.999.999.999",
        "192.168.1.256",
        "192.168.1",
        "not-an-ip",
        "",
        "192.168.1.1/33",
        "2001:db8::1/129",
    ],
)
def test_normalize_ip_invalid(invalid_ip):
    with pytest.raises(ValueError):
        health_check.normalize_ip(invalid_ip)

def test_netbox_get_missing_token():
    with patch.object(health_check, "NETBOX_TOKEN", ""):
        with pytest.raises(RuntimeError, match="NETBOX_TOKEN is not set"):
            health_check.netbox_get("/api/dcim/devices/")


def test_netbox_get_success():
    expected = {
        "count": 1,
        "results": [{"name": "R1"}],
    }

    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(expected).encode("utf-8")

    mock_context = MagicMock()
    mock_context.__enter__.return_value = mock_response

    with patch.object(health_check, "NETBOX_TOKEN", "test-token"), \
         patch.object(health_check, "urlopen", return_value=mock_context) as mock_urlopen:

        result = health_check.netbox_get("/api/dcim/devices/")

    assert result == expected
    mock_urlopen.assert_called_once()

    request = mock_urlopen.call_args.args[0]
    assert request.full_url == (
        f"{health_check.NETBOX_URL}/api/dcim/devices/"
    )
    assert request.get_header("Authorization") == "Bearer test-token"


def test_netbox_get_network_error():
    with patch.object(health_check, "NETBOX_TOKEN", "test-token"), \
         patch.object(health_check, "urlopen", side_effect=URLError("Connection refused")):

        with pytest.raises(URLError, match="Connection refused"):
            health_check.netbox_get("/api/dcim/devices/")

def make_device(
    primary_ip4=None,
    primary_ip6=None,
    username="admin",
    secret_ref="test-secret",
    manufacturer="Arista",
):
    return {
        "primary_ip4": primary_ip4,
        "primary_ip6": primary_ip6,
        "custom_fields": {
            "management_username": username,
            "secret_ref": secret_ref,
        },
        "device_type": {
            "manufacturer": {"name": manufacturer},
        },
    }


def test_get_device_success():
    device = make_device(primary_ip4={"address": "172.20.20.6/24"})

    with patch.object(health_check, "netbox_get",
                      return_value={"results": [device]}) as mock_get:
        result = health_check.get_device("R1")

    assert result == {
        "name": "R1",
        "mgmt_ip": "172.20.20.6",
        "username": "admin",
        "secret_ref": "test-secret",
        "manufacturer": "arista",
    }
    mock_get.assert_called_once_with("/api/dcim/devices/?name=R1")


def test_get_device_ipv6():
    device = make_device(
        primary_ip6={"address": "2001:db8::6/64"}
    )
    with patch.object(health_check, "netbox_get",
                      return_value={"results": [device]}):
        result = health_check.get_device("R1")

    assert result["mgmt_ip"] == "2001:db8::6"


@pytest.mark.parametrize("results", [
    [],
    [{}, {}],
])
def test_get_device_not_unique(results):
    with patch.object(health_check, "netbox_get",
                      return_value={"results": results}):
        with pytest.raises(RuntimeError, match="not uniquely found"):
            health_check.get_device("R1")


def test_get_device_no_primary_ip():
    device = make_device()

    with patch.object(health_check, "netbox_get",
                      return_value={"results": [device]}):
        with pytest.raises(RuntimeError, match="no primary IP"):
            health_check.get_device("R1")


@pytest.mark.parametrize("username,secret_ref,error", [
    ("", "test-secret", "management_username missing"),
    ("admin", "", "secret_ref missing"),
])
def test_get_device_missing_credentials(username, secret_ref, error):
    device = make_device(
        primary_ip4={"address": "172.20.20.6/24"},
        username=username,
        secret_ref=secret_ref,
    )

    with patch.object(health_check, "netbox_get",
                      return_value={"results": [device]}):
        with pytest.raises(RuntimeError, match=error):
            health_check.get_device("R1")


def test_get_device_missing_manufacturer():
    device = make_device(
        primary_ip4={"address": "172.20.20.6/24"},
        manufacturer="",
    )

    with patch.object(health_check, "netbox_get",
                      return_value={"results": [device]}):
        result = health_check.get_device("R1")

    assert result["manufacturer"] == ""

def test_get_all_device_names():
    data = {
        "results": [
            {"name": "R3", "device_type": {
                "manufacturer": {"name": "Nokia"}}},
            {"name": "R1", "device_type": {
                "manufacturer": {"name": "Arista"}}},
            {"name": "FW1", "device_type": {
                "manufacturer": {"name": "Cisco"}}},
            {"name": "R2", "device_type": {
                "manufacturer": {"name": "ARISTA"}}},
            {"name": "", "device_type": {
                "manufacturer": {"name": "Nokia"}}},
            {"name": "Unknown", "device_type": {}},
        ]
    }

    with patch.object(
        health_check, "netbox_get", return_value=data
    ) as mock_get:
        result = health_check.get_all_device_names()

    assert result == ["R1", "R2", "R3"]
    mock_get.assert_called_once_with(
        "/api/dcim/devices/?limit=1000"
    )


def test_get_all_device_names_empty():
    with patch.object(
        health_check, "netbox_get",
        return_value={"results": []}
    ):
        assert health_check.get_all_device_names() == []


@pytest.mark.parametrize("manufacturer, expected", [
    ("arista", "arista"),
    ("nokia", "nokia"),
    ("arista networks", "arista"),
])
def test_detect_platform_valid(manufacturer, expected):
    result = health_check.detect_platform(
        {"manufacturer": manufacturer}
    )
    assert result == expected


def test_detect_platform_unsupported():
    with pytest.raises(
        RuntimeError, match="Unsupported manufacturer"
    ):
        health_check.detect_platform(
            {"manufacturer": "cisco"}
        )


def test_detect_platform_missing_manufacturer():
    with pytest.raises(KeyError):
        health_check.detect_platform({})

@pytest.mark.parametrize(
    "platform, output, expected",
    [
        (
            "arista",
            "10.0.0.1 4 65001 100 100 0 0 00:10:00 5\n"
            "10.0.0.2 4 65002 100 100 0 0 00:10:00 8",
            {"status": "pass", "established": 2, "total": 2},
        ),
        (
            "arista",
            "10.0.0.1 4 65001 0 0 0 0 00:00:00 Idle",
            {"status": "fail", "established": 0, "total": 1},
        ),
        (
            "arista",
            "10.0.0.1 4 65001 100 100 0 0 00:10:00 5\n"
            "10.0.0.2 4 65002 0 0 0 0 00:00:00 Active",
            {"status": "warn", "established": 1, "total": 2},
        ),
        (
            "arista",
            "",
            {"status": "unknown", "established": 0, "total": 0},
        ),
        (
            "nokia",
            "3 configured neighbors\n"
            "3 configured sessions are established",
            {"status": "pass", "established": 3, "total": 3},
        ),
        (
            "nokia",
            "3 configured neighbors\n"
            "0 configured sessions are established",
            {"status": "fail", "established": 0, "total": 3},
        ),
        (
            "nokia",
            "3 configured neighbors\n"
            "2 configured sessions are established",
            {"status": "warn", "established": 2, "total": 3},
        ),
        (
            "nokia",
            "",
            {"status": "unknown", "established": 0, "total": 0},
        ),
    ],
)
def test_parse_bgp_health(platform, output, expected):
    result = health_check.parse_bgp_health(output, platform)
    assert result == expected

@pytest.mark.parametrize("platform,output,expected", [
    ("arista", "O 10.0.1.0/24 via 10.0.0.1", 1),
    ("arista", "O 10.0.1.0/24\nB 10.0.2.0/24", 2),
    ("arista", "No routes found", 0),
    ("nokia", "10.0.13.0/30 ospfv2", 1),
    ("nokia", "10.0.13.0/30 ospfv2\n172.31.255.1/32 ospfv2", 2),
    ("nokia", "No routes found", 0),
])
def test_parse_route_health(platform, output, expected):
    result = health_check.parse_route_health(output, platform)
    assert result["routes"] == expected
    assert result["status"] == ("pass" if expected > 0 else "fail")


@pytest.mark.parametrize("output,expected_status,expected_cpu", [
    ("CPU utilization: 30%", "pass", 30.0),
    ("CPU utilization: 69.99%", "pass", 69.99),
    ("CPU utilization: 70%", "warn", 70.0),
    ("CPU utilization: 85%", "warn", 85.0),
    ("CPU utilization: 90%", "warn", 90.0),
    ("CPU utilization: 95%", "fail", 95.0),
    ("Cpu(s): 80.0 id", "pass", 20.0),
    ("No CPU data", "unknown", None),
])
def test_parse_cpu_health(output, expected_status, expected_cpu):
    result = health_check.parse_cpu_health(output, "arista")
    assert result["status"] == expected_status
    assert result["percent"] == expected_cpu


@pytest.mark.parametrize("statuses,expected", [
    (["pass"] * 5, "healthy"),
    (["pass", "warn", "pass", "pass", "pass"], "warning"),
    (["pass", "unknown", "pass", "pass", "pass"], "warning"),
    (["pass", "fail", "pass", "pass", "pass"], "unhealthy"),
    (["pass", "warn", "fail", "pass", "pass"], "unhealthy"),
    (["pass", "pass", None, "pass", "pass"], "warning"),
])
def test_calculate_overall(statuses, expected):
    keys = [
        "connectivity",
        "ssh",
        "neighbor_health",
        "route_health",
        "cpu_health",
    ]

    result = {
        key: {"status": status}
        for key, status in zip(keys, statuses)
    }

    assert health_check.calculate_overall(result) == expected

def test_connect_success():
    device = {
        "mgmt_ip": "172.20.20.6",
        "username": "admin",
        "secret_ref": "test-secret",
    }

    with patch.object(
        health_check, "get_password", return_value="fake-password"
    ) as mock_password, patch.object(
        health_check.paramiko, "SSHClient"
    ) as mock_client:
        result = health_check.connect(device)

    mock_password.assert_called_once_with("test-secret")
    mock_client.return_value.connect.assert_called_once_with(
        hostname="172.20.20.6",
        username="admin",
        password="fake-password",
        look_for_keys=False,
        allow_agent=False,
        timeout=10,
        auth_timeout=10,
        banner_timeout=10,
    )
    assert result is mock_client.return_value


def test_connect_failure():
    device = {
        "mgmt_ip": "172.20.20.6",
        "username": "admin",
        "secret_ref": "test-secret",
    }

    with patch.object(
        health_check, "get_password", return_value="fake-password"
    ), patch.object(
        health_check.paramiko, "SSHClient"
    ) as mock_client:
        mock_client.return_value.connect.side_effect = (
            health_check.paramiko.AuthenticationException()
        )

        with pytest.raises(
            health_check.paramiko.AuthenticationException
        ):
            health_check.connect(device)


def test_read_channel():
    channel = MagicMock()
    channel.recv_ready.side_effect = [True, True, False]
    channel.recv.side_effect = [b"Hello ", b"World"]

    with patch.object(health_check.time, "sleep"):
        result = health_check.read_channel(channel, delay=0)

    assert result == "Hello World"
    assert channel.recv.call_count == 2


def test_run_shell_command_success():
    channel = MagicMock()
    channel.recv_ready.side_effect = [True, False]
    channel.recv.return_value = b"show version\nEOS 4.36.2F\nR1#"

    with patch.object(
        health_check.time, "sleep"
    ), patch.object(
        health_check.time, "monotonic",
        side_effect=[0, 0, 0, 1]
    ):
        result = health_check.run_shell_command(
            channel, "show version"
        )

    channel.send.assert_called_once_with("show version\n")
    assert "EOS 4.36.2F" in result
    assert result.endswith("R1#")


def test_run_shell_command_timeout():
    channel = MagicMock()
    channel.recv_ready.return_value = False

    with patch.object(
        health_check.time, "sleep"
    ), patch.object(
        health_check.time, "monotonic",
        side_effect=[0, 0, 10]
    ):
        result = health_check.run_shell_command(
            channel, "show version", timeout=8
        )

    channel.send.assert_called_once_with("show version\n")
    assert result == ""

@pytest.mark.parametrize("platform,commands", [
    ("arista", [
        "terminal length 0",
        "show ip bgp summary",
        "show ip route",
        "show processes top once",
    ]),
    ("nokia", [
        "show network-instance default protocols bgp neighbor",
        "show network-instance default ipv4 route all",
    ]),
])
def test_platform_health_success(platform, commands):
    device = {"name": "R1"}
    client = MagicMock()
    channel = client.invoke_shell.return_value

    with patch.object(health_check, "connect", return_value=client), \
         patch.object(health_check, "read_channel"), \
         patch.object(health_check, "run_shell_command") as mock_run:

        mock_run.side_effect = [
            f"output-{i}" for i in range(len(commands))
        ]

        if platform == "arista":
            result = health_check.check_arista(device)
        else:
            result = health_check.check_nokia(device)

    actual_commands = [
        call.args[1] for call in mock_run.call_args_list
    ]
    assert actual_commands == commands
    assert result["neighbors"]["command"] == commands[
        1 if platform == "arista" else 0
    ]
    assert result["routing_table"]["output"] == (
        "output-2" if platform == "arista" else "output-1"
    )
    if platform == "arista":
        assert result["cpu"]["output"] == "output-3"
    else:
        assert "cpu" not in result

    client.close.assert_called_once()
    client.invoke_shell.assert_called_once()


@pytest.mark.parametrize("platform", ["arista", "nokia"])
def test_platform_health_closes_on_error(platform):
    client = MagicMock()

    with patch.object(health_check, "connect", return_value=client), \
         patch.object(health_check, "read_channel"), \
         patch.object(
             health_check,
             "run_shell_command",
             side_effect=RuntimeError("SSH command failed"),
         ):

        with pytest.raises(RuntimeError, match="SSH command failed"):
            if platform == "arista":
                health_check.check_arista({"name": "R1"})
            else:
                health_check.check_nokia({"name": "R3"})

    client.close.assert_called_once()

@pytest.mark.parametrize("platform", ["arista", "nokia"])
def test_check_device_success(platform):
    device = {
        "name": "R1",
        "mgmt_ip": "172.20.20.6",
        "manufacturer": platform,
    }
    checks = {
        "neighbors": {"output": "bgp-output"},
        "routing_table": {"output": "route-output"},
    }
    if platform == "arista":
        checks["cpu"] = {"output": "cpu-output"}

    ping_result = {"status": "pass", "reachable": True}
    bgp_result = {"status": "pass"}
    route_result = {"status": "pass"}
    cpu_result = {"status": "pass"}

    with patch.object(health_check, "get_device", return_value=device), \
         patch.object(health_check, "detect_platform", return_value=platform), \
         patch.object(health_check, "check_ping", return_value=ping_result) as ping, \
         patch.object(health_check, "check_arista", return_value=checks) as arista, \
         patch.object(health_check, "check_nokia", return_value=checks) as nokia, \
         patch.object(health_check, "parse_bgp_health", return_value=bgp_result) as bgp, \
         patch.object(health_check, "parse_route_health", return_value=route_result) as route, \
         patch.object(health_check, "parse_cpu_health", return_value=cpu_result) as cpu, \
         patch.object(health_check, "get_cpu_from_influx", return_value=cpu_result) as influx, \
         patch.object(health_check, "calculate_overall", return_value="healthy") as overall:

        result = health_check.check_device("R1")

    assert result["device"] == "R1"
    assert result["management_ip"] == "172.20.20.6"
    assert result["platform"] == platform
    assert result["connectivity"] == ping_result
    assert result["ssh"]["status"] == "pass"
    assert result["neighbor_health"] == bgp_result
    assert result["route_health"] == route_result
    assert result["cpu_health"] == cpu_result
    assert result["overall"] == "healthy"

    ping.assert_called_once_with("172.20.20.6")
    bgp.assert_called_once_with("bgp-output", platform)
    route.assert_called_once_with("route-output", platform)
    overall.assert_called_once()

    if platform == "arista":
        arista.assert_called_once_with(device)
        nokia.assert_not_called()
        cpu.assert_called_once_with("cpu-output", "arista")
        influx.assert_not_called()
    else:
        nokia.assert_called_once_with(device)
        arista.assert_not_called()
        influx.assert_called_once_with("R1")
        cpu.assert_not_called()


def test_check_device_ssh_failure():
    device = {
        "name": "R1",
        "mgmt_ip": "172.20.20.6",
        "manufacturer": "arista",
    }

    with patch.object(health_check, "get_device", return_value=device), \
         patch.object(health_check, "detect_platform", return_value="arista"), \
         patch.object(
             health_check, "check_ping",
             return_value={"status": "pass", "reachable": True}
         ), \
         patch.object(
             health_check, "check_arista",
             side_effect=RuntimeError("SSH connection refused")
         ), \
         patch.object(
             health_check, "calculate_overall",
             return_value="unhealthy"
         ) as overall:

        result = health_check.check_device("R1")

    assert result["ssh"]["status"] == "fail"
    assert "SSH connection refused" in result["ssh"]["error"]
    assert result["overall"] == "unhealthy"
    overall.assert_called_once()

def test_influx_query_missing_token():
    with patch.object(health_check, "INFLUX_TOKEN", ""):
        with pytest.raises(RuntimeError, match="INFLUX_TOKEN is not set"):
            health_check.influx_query("from(bucket: \"nmas\")")


def test_influx_query_success():
    csv_data = "_time,_value\n2026-10-19T12:00:00Z,35.5\n"

    response = MagicMock()
    response.read.return_value = csv_data.encode("utf-8")
    context = MagicMock()
    context.__enter__.return_value = response

    with patch.object(health_check, "INFLUX_TOKEN", "test-token"), \
         patch.object(health_check, "urlopen", return_value=context) as mock_open:

        result = health_check.influx_query("test-flux")

    assert result == csv_data
    request = mock_open.call_args.args[0]
    assert request.get_method() == "POST"
    assert request.data == b"test-flux"
    assert request.get_header("Authorization") == "Token test-token"


@pytest.mark.parametrize("cpu,expected", [
    (30, "pass"),
    (69.99, "pass"),
    (70, "warn"),
    (85, "warn"),
    (90, "warn"),
    (95, "fail"),
])
def test_get_cpu_from_influx(cpu, expected):
    csv_data = (
        "_time,_value\n"
        f"2026-10-19T12:00:00Z,{cpu}\n"
    )

    with patch.object(
        health_check, "influx_query", return_value=csv_data
    ) as mock_query:
        result = health_check.get_cpu_from_influx("R3")

    assert result["status"] == expected
    assert result["percent"] == cpu
    assert result["source"] == "influxdb"
    assert result["time"] == "2026-10-19T12:00:00Z"
    assert 'r3_cpu' in mock_query.call_args.args[0]


def test_get_cpu_from_influx_empty():
    with patch.object(
        health_check, "influx_query",
        return_value="_time,_value\n"
    ):
        result = health_check.get_cpu_from_influx("R3")

    assert result["status"] == "unknown"
    assert result["percent"] is None


def test_save_report(tmp_path):
    data = [{"device": "R1", "overall": "healthy"}]
    report_dir = tmp_path / "health"

    with patch.object(health_check, "REPORT_DIR", report_dir):
        filename = health_check.save_report(data)

    assert filename.exists()
    assert json.loads(filename.read_text()) == data

    latest = report_dir / "health-latest.json"
    assert latest.exists()
    assert json.loads(latest.read_text()) == data