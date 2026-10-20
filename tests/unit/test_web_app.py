import pytest
import yaml
import json

from unittest.mock import MagicMock
from urllib.error import URLError

from web import app as web_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    host_vars = tmp_path / "inventory" / "host_vars"
    host_vars.mkdir(parents=True)

    reports = tmp_path / "reports"
    reports.mkdir()

    health_dir = reports / "health"
    health_dir.mkdir()

    config_changes_dir = reports / "config_changes"
    config_changes_dir.mkdir()

    monkeypatch.setattr(web_app, "HOST_VARS", host_vars)

    # Isolate report paths from the real Lab4 environment.
    monkeypatch.setattr(
        web_app,
        "HEALTH_REPORT",
        health_dir / "health-latest.json",
    )

    monkeypatch.setattr(
        web_app,
        "CONFIG_CHANGE_DIR",
        config_changes_dir,
    )

    web_app.app.config.update(TESTING=True)

    with web_app.app.test_client() as client:
        yield client


@pytest.mark.parametrize("hostname,expected", [
    ("R1", True),
    ("R-1", True),
    ("Switch_1", True),
    ("R1.example.com", False),
    ("../R1", False),
    ("R1;rm", False),
    ("", False),
])
def test_valid_hostname(hostname, expected):
    assert web_app.valid_hostname(hostname) is expected


def test_clean_list():
    result = web_app.clean_list([
        " Ethernet1 ", "", " ", "Ethernet2"
    ])
    assert result == ["Ethernet1", "Ethernet2"]


@pytest.mark.parametrize("path", [
    "/",
    "/devices",
    "/devices/new",
    "/monitoring",
    "/topology",
    "/health",
    "/config-changes",
])
def test_dashboard_pages(client, path):
    response = client.get(path)
    assert response.status_code == 200


def test_load_device_yaml_success(tmp_path, monkeypatch):
    host_vars = tmp_path / "host_vars"
    host_vars.mkdir()
    monkeypatch.setattr(web_app, "HOST_VARS", host_vars)

    yaml_file = host_vars / "R1.yml"
    yaml_file.write_text(
        yaml.safe_dump({
            "hostname": "R1",
            "vendor": "arista",
            "platform": "ceos",
        })
    )

    with web_app.app.test_request_context():
        data, path = web_app.load_device_yaml("R1")

    assert data["hostname"] == "R1"
    assert data["vendor"] == "arista"
    assert path == yaml_file


@pytest.mark.parametrize("device,status", [
    ("../R1", 400),
    ("R1.example.com", 400),
    ("NONEXISTENT", 404),
])
def test_load_device_yaml_invalid(client, device, status):
    with web_app.app.test_request_context():
        with pytest.raises(Exception) as exc:
            web_app.load_device_yaml(device)

    assert exc.value.code == status


def test_devices_list(client, tmp_path):
    host_vars = web_app.HOST_VARS
    (host_vars / "R1.yml").write_text(
        yaml.safe_dump({
            "hostname": "R1",
            "vendor": "arista",
            "platform": "ceos",
        })
    )
    (host_vars / "R3.yml").write_text(
        yaml.safe_dump({
            "hostname": "R3",
            "vendor": "nokia",
            "platform": "srlinux",
        })
    )

    response = client.get("/devices")

    assert response.status_code == 200
    assert b"R1" in response.data
    assert b"R3" in response.data

from types import SimpleNamespace
from unittest.mock import patch


def device_form(**overrides):
    data = {
        "hostname": "TEST1",
        "vendor": "arista",
        "platform": "ceos",
        "management_ip": "192.0.2.10/24",
        "routing_protocol": "",
        "router_id": "",
        "bgp_as": "",
        "loopback_ipv4": "",
        "loopback_ipv6": "",
        "interface_name[]": ["Ethernet1"],
        "interface_ipv4[]": ["10.0.0.1/30"],
        "interface_ipv6[]": [""],
    }
    data.update(overrides)
    return data


def test_new_device_success(client):
    with patch.object(web_app.subprocess, "run") as run:
        run.return_value = SimpleNamespace(
            returncode=0, stderr=""
        )
        response = client.post(
            "/devices/new", data=device_form()
        )

    assert response.status_code == 302
    saved = web_app.HOST_VARS / "TEST1.yml"
    assert saved.exists()

    config = yaml.safe_load(saved.read_text())
    assert config["hostname"] == "TEST1"
    assert config["vendor"] == "arista"
    assert config["interfaces"][0]["mode"] == "routed"
    assert config["interfaces"][0]["ipv4"] == "10.0.0.1/30"

    run.assert_called_once()


def test_new_device_invalid_hostname(client):
    with patch.object(web_app.subprocess, "run") as run:
        response = client.post(
            "/devices/new",
            data=device_form(hostname="../TEST1"),
        )

    assert response.status_code == 302
    assert list(web_app.HOST_VARS.glob("*.yml")) == []
    run.assert_not_called()


def test_new_device_duplicate(client):
    (web_app.HOST_VARS / "TEST1.yml").write_text(
        "hostname: TEST1\n"
    )

    with patch.object(web_app.subprocess, "run") as run:
        response = client.post(
            "/devices/new", data=device_form()
        )

    assert response.status_code == 302
    assert (web_app.HOST_VARS / "TEST1.yml").read_text() == (
        "hostname: TEST1\n"
    )
    run.assert_not_called()


def test_new_device_render_failure(client):
    with patch.object(web_app.subprocess, "run") as run:
        run.return_value = SimpleNamespace(
            returncode=1, stderr="Template error"
        )
        response = client.post(
            "/devices/new", data=device_form()
        )

    assert response.status_code == 302
    assert (web_app.HOST_VARS / "TEST1.yml").exists()
    run.assert_called_once()

def post_device(client, **overrides):
    with patch.object(web_app.subprocess, "run") as run:
        run.return_value = SimpleNamespace(
            returncode=0, stderr=""
        )
        response = client.post(
            "/devices/new", data=device_form(**overrides)
        )
    return response, run


def test_new_arista_ospf(client):
    response, run = post_device(
        client,
        routing_protocol="ospf",
        router_id="1.1.1.1",
        loopback_ipv4="10.255.0.1/32",
        loopback_ipv6="2001:db8::1/128",
        **{"interface_ipv6[]": ["2001:db8:1::1/64"]},
    )

    assert response.status_code == 302
    data = yaml.safe_load(
        (web_app.HOST_VARS / "TEST1.yml").read_text()
    )

    assert data["routing"]["router_id"] == "1.1.1.1"
    assert data["routing"]["ospf"]["process"] == 1
    assert data["interfaces"][0]["ospf_area"] == "0.0.0.0"
    assert data["interfaces"][0]["ospfv3_process"] == 1
    assert data["loopback"]["ospf_area"] == "0.0.0.0"
    assert data["loopback"]["ipv6"] == "2001:db8::1/128"
    run.assert_called_once()


def test_new_nokia_ospf(client):
    response, run = post_device(
        client,
        vendor="nokia",
        platform="srlinux",
        routing_protocol="ospf",
        router_id="3.3.3.3",
        loopback_ipv4="10.255.0.3/32",
        **{"interface_name[]": ["ethernet-1/1"]},
    )

    assert response.status_code == 302
    data = yaml.safe_load(
        (web_app.HOST_VARS / "TEST1.yml").read_text()
    )

    assert data["ospf"]["router_id"] == "3.3.3.3"
    assert data["ospf"]["interfaces"] == [
        "ethernet-1/1.0", "lo0.0"
    ]
    assert "mode" not in data["interfaces"][0]
    assert "prefix_sets" in data
    run.assert_called_once()


def test_new_nokia_bgp(client):
    response, run = post_device(
        client,
        vendor="nokia",
        platform="srlinux",
        routing_protocol="bgp",
        router_id="3.3.3.3",
        bgp_as="65003",
        **{
            "neighbor_address[]": ["10.0.0.1", ""],
            "neighbor_as[]": ["65001", ""],
        },
    )

    assert response.status_code == 302
    data = yaml.safe_load(
        (web_app.HOST_VARS / "TEST1.yml").read_text()
    )

    assert data["bgp"]["local_as"] == 65003
    assert data["bgp"]["router_id"] == "3.3.3.3"
    assert data["bgp"]["neighbors"] == [
        {"address": "10.0.0.1", "peer_as": 65001}
    ]
    assert data["bgp"]["ipv4_export_policy"] == "WEB-IPV4-EXPORT"
    assert "prefix_sets" in data
    run.assert_called_once()


@pytest.mark.parametrize("overrides", [
    {"routing_protocol": "ospf", "router_id": ""},
    {
        "routing_protocol": "bgp",
        "router_id": "1.1.1.1",
        "bgp_as": "",
    },
    {
        "routing_protocol": "bgp",
        "router_id": "1.1.1.1",
        "bgp_as": "65001",
        "neighbor_address[]": ["10.0.0.2"],
        "neighbor_as[]": [""],
    },
])
def test_new_device_invalid_routing(client, overrides):
    response, run = post_device(client, **overrides)

    assert response.status_code == 302
    assert not (web_app.HOST_VARS / "TEST1.yml").exists()
    run.assert_not_called()

@pytest.fixture
def existing_device(client, tmp_path, monkeypatch):
    (web_app.HOST_VARS / "R1.yml").write_text(
        "hostname: R1\nvendor: arista\nplatform: ceos\n"
    )

    generated = tmp_path / "generated"
    golden = tmp_path / "golden"
    generated.mkdir()
    golden.mkdir()

    monkeypatch.setattr(web_app, "GENERATED_DIR", generated)
    monkeypatch.setattr(web_app, "GOLDEN_DIR", golden)
    return client, generated, golden


def test_view_yaml(existing_device):
    client, _, _ = existing_device
    response = client.get("/devices/R1/yaml")

    assert response.status_code == 200
    assert b"hostname: R1" in response.data


@pytest.mark.parametrize("returncode", [0, 1])
def test_render_device_route(existing_device, returncode):
    client, _, _ = existing_device

    with patch.object(web_app.subprocess, "run") as run:
        run.return_value = SimpleNamespace(
            returncode=returncode, stderr="Render error"
        )
        response = client.post("/devices/R1/render")

    assert response.status_code == 302
    run.assert_called_once()
    assert run.call_args.args[0][-1] == "R1"


@pytest.mark.parametrize("returncode", [0, 1])
def test_golden_device_route(existing_device, returncode):
    client, _, _ = existing_device

    with patch.object(web_app.subprocess, "run") as run:
        run.return_value = SimpleNamespace(
            returncode=returncode, stderr="Save error"
        )
        response = client.post("/devices/R1/golden")

    assert response.status_code == 302
    run.assert_called_once()
    assert run.call_args.args[0][-1] == "R1"


def test_view_generated_success(existing_device):
    client, generated, _ = existing_device
    (generated / "R1.cfg").write_text("hostname R1\n")

    response = client.get("/devices/R1/generated")

    assert response.status_code == 200
    assert b"hostname R1" in response.data


def test_view_generated_missing(existing_device):
    client, _, _ = existing_device
    response = client.get("/devices/R1/generated")

    assert response.status_code == 302


def test_golden_history(existing_device):
    client, _, golden = existing_device
    device_dir = golden / "R1"
    device_dir.mkdir()

    (device_dir / "R1-20261019.cfg").write_text("hostname R1\n")

    response = client.get("/devices/R1/golden/history")

    assert response.status_code == 200
    assert b"R1-20261019.cfg" in response.data


def test_view_golden_success(existing_device):
    client, _, golden = existing_device
    device_dir = golden / "R1"
    device_dir.mkdir()
    (device_dir / "R1-latest.cfg").write_text("hostname R1\n")

    response = client.get("/devices/R1/golden/R1-latest.cfg")

    assert response.status_code == 200
    assert b"hostname R1" in response.data


def test_view_golden_missing(existing_device):
    client, _, _ = existing_device

    response = client.get("/devices/R1/golden/missing.cfg")

    assert response.status_code == 404

def test_topology_api_success(client):
    topology = {"nodes": [{"id": "R1"}], "edges": []}
    response = MagicMock()
    response.read.return_value = json.dumps(topology).encode()
    response.__enter__.return_value = response

    with patch("urllib.request.urlopen", return_value=response) as mock:
        result = client.get("/api/topology")

    assert result.status_code == 200
    assert result.get_json() == topology
    assert mock.call_args.kwargs["timeout"] == 20


def test_topology_api_failure(client):
    with patch(
        "urllib.request.urlopen",
        side_effect=URLError("Bridge unavailable"),
    ):
        result = client.get("/api/topology")

    assert result.status_code == 500
    assert "Bridge unavailable" in result.get_json()["error"]


def test_health_report(client, tmp_path, monkeypatch):
    report = tmp_path / "health-latest.json"
    report.write_text(json.dumps([
        {"device": "R1", "overall": "healthy"}
    ]))
    monkeypatch.setattr(web_app, "HEALTH_REPORT", report)

    response = client.get("/health")

    assert response.status_code == 200
    assert b"R1" in response.data


def test_config_change_reports(client, tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "CONFIG_CHANGE_DIR", tmp_path)

    for device in ("R1", "R3"):
        (tmp_path / f"{device}-latest.json").write_text(
            json.dumps({"device": device, "changed": True})
        )

    response = client.get("/config-changes")

    assert response.status_code == 200
    assert b"R1" in response.data
    assert b"R3" in response.data


@pytest.mark.parametrize("route,script,redirect_path", [
    ("/health/run", "health_check.py", "/health"),
    ("/config-changes/run", "config_change.py", "/config-changes"),
])
def test_run_automation_routes(client, route, script, redirect_path):
    with patch.object(web_app.subprocess, "run") as mock_run:
        response = client.post(route)

    assert response.status_code == 302
    assert response.headers["Location"].endswith(redirect_path)
    mock_run.assert_called_once()

    args = mock_run.call_args
    assert args.args[0][-2].endswith(script)
    assert args.args[0][-1] == "--all"
    assert args.kwargs["check"] is False