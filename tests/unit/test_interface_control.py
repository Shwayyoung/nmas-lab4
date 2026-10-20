import sys
import types
import json
from unittest.mock import MagicMock, patch, call
from urllib.error import URLError

import pytest

# Jenkins does not have the real credentials.py
fake_credentials = types.ModuleType("credentials")
fake_credentials.get_password = lambda *args, **kwargs: "test-only"
sys.modules.setdefault("credentials", fake_credentials)

from automation import interface_control as ic


@pytest.mark.parametrize("interface", [
    "Management0", "Management1", "mgmt0", "mgmt1",
    "MANAGEMENT0",
])
def test_block_management_interface(interface):
    with pytest.raises(RuntimeError, match="Refusing to modify"):
        ic.validate_interface(interface)


@pytest.mark.parametrize("interface", [
    "Ethernet1", "Ethernet2", "ethernet-1/1", "loopback0",
])
def test_allow_network_interface(interface):
    assert ic.validate_interface(interface) is None


@pytest.mark.parametrize("action,command", [
    ("disable", "shutdown"),
    ("enable", "no shutdown"),
])
def test_control_arista(action, command):
    client = MagicMock()
    channel = client.invoke_shell.return_value

    with patch.object(ic, "connect", return_value=client), \
         patch.object(ic, "read_channel", return_value=""), \
         patch.object(ic, "send_command", return_value="OK") as send:
        output = ic.control_arista(
            {"name": "R1"}, "Ethernet1", action
        )

    commands = [c.args[1] for c in send.call_args_list]

    assert commands == [
        "enable",
        "configure terminal",
        "interface Ethernet1",
        command,
        "end",
        "write memory",
    ]
    assert send.call_args_list[-1] == call(
        channel, "write memory", delay=1.5
    )
    assert output == "OK" * 6
    client.close.assert_called_once()


@pytest.mark.parametrize("action,state", [
    ("disable", "disable"),
    ("enable", "enable"),
])
def test_control_nokia(action, state):
    client = MagicMock()
    channel = client.invoke_shell.return_value

    with patch.object(ic, "connect", return_value=client), \
         patch.object(ic, "read_channel", return_value=""), \
         patch.object(ic, "send_command", return_value="OK") as send:
        output = ic.control_nokia(
            {"name": "R3"}, "ethernet-1/1", action
        )

    assert [c.args[1] for c in send.call_args_list] == [
        "enter candidate",
        f"interface ethernet-1/1 admin-state {state}",
        "commit now",
    ]
    assert send.call_args_list[-1] == call(
        channel, "commit now", delay=1.5
    )
    assert output == "OK" * 3
    client.close.assert_called_once()


@pytest.mark.parametrize("platform,expected_function", [
    ("arista", "control_arista"),
    ("nokia", "control_nokia"),
])
def test_control_interface_dispatch(platform, expected_function):
    device = {"name": "R1", "manufacturer": platform}

    with patch.object(ic, "get_device", return_value=device), \
         patch.object(ic, "control_arista") as arista, \
         patch.object(ic, "control_nokia") as nokia:
        ic.control_interface("R1", "Ethernet1", "disable")

    if expected_function == "control_arista":
        arista.assert_called_once_with(
            device, "Ethernet1", "disable"
        )
        nokia.assert_not_called()
    else:
        nokia.assert_called_once_with(
            device, "Ethernet1", "disable"
        )
        arista.assert_not_called()


def test_management_interface_never_connects():
    with patch.object(ic, "get_device") as get_device, \
         patch.object(ic, "connect") as connect:
        with pytest.raises(RuntimeError):
            ic.control_interface(
                "R1", "Management0", "disable"
            )

    get_device.assert_not_called()
    connect.assert_not_called()

@pytest.mark.parametrize("error", [
    "% Invalid input",
    "% Error occurred",
    "Incomplete command",
    "Ambiguous command",
])
def test_arista_cli_error(error):
    client = MagicMock()

    with patch.object(ic, "connect", return_value=client), \
         patch.object(ic, "read_channel", return_value=""), \
         patch.object(ic, "send_command", return_value=error):

        with pytest.raises(
            RuntimeError, match="EOS returned a CLI error"
        ):
            ic.control_arista(
                {"name": "R1"}, "Ethernet1", "disable"
            )

    client.close.assert_called_once()


@pytest.mark.parametrize("error", [
    "Parsing error",
    "Error:",
    "Invalid",
    "failed",
    "Failed",
])
def test_nokia_cli_error(error):
    client = MagicMock()

    with patch.object(ic, "connect", return_value=client), \
         patch.object(ic, "read_channel", return_value=""), \
         patch.object(ic, "send_command", return_value=error):

        with pytest.raises(
            RuntimeError, match="SR Linux returned a CLI error"
        ):
            ic.control_nokia(
                {"name": "R3"}, "ethernet-1/1", "disable"
            )

    client.close.assert_called_once()


@pytest.mark.parametrize("platform", ["arista", "nokia"])
def test_interface_ssh_command_exception(platform):
    client = MagicMock()

    with patch.object(ic, "connect", return_value=client), \
         patch.object(ic, "read_channel", return_value=""), \
         patch.object(
             ic, "send_command",
             side_effect=RuntimeError("SSH disconnected")
         ):

        with pytest.raises(
            RuntimeError, match="SSH disconnected"
        ):
            if platform == "arista":
                ic.control_arista(
                    {"name": "R1"}, "Ethernet1", "disable"
                )
            else:
                ic.control_nokia(
                    {"name": "R3"}, "ethernet-1/1", "disable"
                )

    client.close.assert_called_once()


@pytest.mark.parametrize("value,expected", [
    ("172.20.20.6", "172.20.20.6"),
    ("172.20.20.6/24", "172.20.20.6"),
    ("2001:db8::1/64", "2001:db8::1"),
])
def test_normalize_ip_valid(value, expected):
    assert ic.normalize_ip(value) == expected


@pytest.mark.parametrize("value", [
    "999.999.999.999",
    "not-an-ip",
    "192.168.1.1/33",
])
def test_normalize_ip_invalid(value):
    with pytest.raises(ValueError):
        ic.normalize_ip(value)


def test_netbox_missing_token():
    with patch.object(ic, "NETBOX_TOKEN", ""):
        with pytest.raises(RuntimeError, match="NETBOX_TOKEN is not set"):
            ic.netbox_get("/api/dcim/devices/")


def test_netbox_success():
    data = {"results": [{"name": "R1"}]}
    response = MagicMock()
    response.read.return_value = json.dumps(data).encode()
    context = MagicMock()
    context.__enter__.return_value = response

    with patch.object(ic, "NETBOX_TOKEN", "test-token"), \
         patch.object(ic, "urlopen", return_value=context) as mock_open:
        result = ic.netbox_get("/api/dcim/devices/")

    assert result == data
    request = mock_open.call_args.args[0]
    assert request.get_header("Authorization") == "Bearer test-token"
    assert mock_open.call_args.kwargs["timeout"] == 10


def test_netbox_network_error():
    with patch.object(ic, "NETBOX_TOKEN", "test-token"), \
         patch.object(ic, "urlopen",
                      side_effect=URLError("Connection refused")):
        with pytest.raises(URLError):
            ic.netbox_get("/api/dcim/devices/")


@pytest.mark.parametrize("manufacturer,expected", [
    ("arista", "arista"),
    ("nokia", "nokia"),
])
def test_detect_platform_valid(manufacturer, expected):
    assert ic.detect_platform(
        {"manufacturer": manufacturer}
    ) == expected


def test_detect_platform_unsupported():
    with pytest.raises(RuntimeError, match="Unsupported manufacturer"):
        ic.detect_platform({"manufacturer": "cisco"})

def make_netbox_device(
    ip4=None, ip6=None,
    username="admin", secret_ref="test-secret",
    manufacturer="Arista",
):
    return {
        "primary_ip4": ip4,
        "primary_ip6": ip6,
        "custom_fields": {
            "management_username": username,
            "secret_ref": secret_ref,
        },
        "device_type": {
            "manufacturer": {"name": manufacturer},
        },
    }


def test_get_device_success():
    device = make_netbox_device(
        ip4={"address": "172.20.20.6/24"}
    )

    with patch.object(
        ic, "netbox_get",
        return_value={"results": [device]}
    ) as mock_get:
        result = ic.get_device("R1")

    assert result == {
        "name": "R1",
        "mgmt_ip": "172.20.20.6",
        "username": "admin",
        "secret_ref": "test-secret",
        "manufacturer": "arista",
    }
    mock_get.assert_called_once_with(
        "/api/dcim/devices/?name=R1"
    )


def test_get_device_ipv6():
    device = make_netbox_device(
        ip6={"address": "2001:db8::6/64"},
        manufacturer="Nokia",
    )

    with patch.object(
        ic, "netbox_get",
        return_value={"results": [device]}
    ):
        result = ic.get_device("R3")

    assert result["mgmt_ip"] == "2001:db8::6"
    assert result["manufacturer"] == "nokia"


@pytest.mark.parametrize("results", [
    [],
    [{}, {}],
])
def test_get_device_not_unique(results):
    with patch.object(
        ic, "netbox_get",
        return_value={"results": results}
    ):
        with pytest.raises(
            RuntimeError, match="not uniquely found"
        ):
            ic.get_device("R1")


def test_get_device_missing_ip():
    device = make_netbox_device()

    with patch.object(
        ic, "netbox_get",
        return_value={"results": [device]}
    ):
        with pytest.raises(
            RuntimeError, match="no primary management IP"
        ):
            ic.get_device("R1")


@pytest.mark.parametrize(
    "username,secret_ref,error",
    [
        ("", "test-secret", "management_username missing"),
        ("admin", "", "secret_ref missing"),
    ],
)
def test_get_device_missing_credentials(
    username, secret_ref, error
):
    device = make_netbox_device(
        ip4={"address": "172.20.20.6/24"},
        username=username,
        secret_ref=secret_ref,
    )

    with patch.object(
        ic, "netbox_get",
        return_value={"results": [device]}
    ):
        with pytest.raises(RuntimeError, match=error):
            ic.get_device("R1")


def test_get_device_missing_manufacturer():
    device = make_netbox_device(
        ip4={"address": "172.20.20.6/24"},
        manufacturer="",
    )

    with patch.object(
        ic, "netbox_get",
        return_value={"results": [device]}
    ):
        result = ic.get_device("R1")

    assert result["manufacturer"] == ""

def test_connect_success():
    device = {
        "mgmt_ip": "172.20.20.6",
        "username": "admin",
        "secret_ref": "test-secret",
    }

    with patch.object(
        ic, "get_password", return_value="fake-password"
    ) as mock_password, patch.object(
        ic.paramiko, "SSHClient"
    ) as mock_client:

        result = ic.connect(device)

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
    mock_client.return_value.set_missing_host_key_policy.assert_called_once()
    assert result is mock_client.return_value


def test_connect_auth_failure():
    device = {
        "mgmt_ip": "172.20.20.6",
        "username": "admin",
        "secret_ref": "test-secret",
    }

    with patch.object(
        ic, "get_password", return_value="fake-password"
    ), patch.object(
        ic.paramiko, "SSHClient"
    ) as mock_client:

        mock_client.return_value.connect.side_effect = (
            ic.paramiko.AuthenticationException()
        )

        with pytest.raises(ic.paramiko.AuthenticationException):
            ic.connect(device)


def test_read_channel_success():
    channel = MagicMock()
    channel.recv_ready.side_effect = [True, True, False]
    channel.recv.side_effect = [b"Hello ", b"World"]

    with patch.object(ic.time, "sleep") as mock_sleep:
        result = ic.read_channel(channel, delay=0)

    assert result == "Hello World"
    assert channel.recv.call_count == 2
    assert mock_sleep.call_count == 3


def test_read_channel_empty():
    channel = MagicMock()
    channel.recv_ready.return_value = False

    with patch.object(ic.time, "sleep"):
        result = ic.read_channel(channel, delay=0)

    assert result == ""
    channel.recv.assert_not_called()


def test_read_channel_invalid_utf8():
    channel = MagicMock()
    channel.recv_ready.side_effect = [True, False]
    channel.recv.return_value = b"Hello\xffWorld"

    with patch.object(ic.time, "sleep"):
        result = ic.read_channel(channel, delay=0)

    assert result == "Hello\ufffdWorld"


def test_send_command():
    channel = MagicMock()

    with patch.object(
        ic, "read_channel", return_value="R1#"
    ) as mock_read:

        result = ic.send_command(
            channel, "show version", delay=1.5
        )

    channel.send.assert_called_once_with("show version\n")
    mock_read.assert_called_once_with(channel, 1.5)
    assert result == "R1#"