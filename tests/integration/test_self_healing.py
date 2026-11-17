import json
import subprocess
import urllib.request


TOPOLOGY_URL = (
    "http://127.0.0.1:8088/api/topology"
)

DEVICES_URL = (
    "http://127.0.0.1:8088/api/devices"
)

SELF_HEALING = (
    "/usr/local/sbin/nmas-self-healing"
)


def get_json(url):

    with urllib.request.urlopen(
        url,
        timeout=10,
    ) as response:

        return json.loads(
            response.read().decode(
                "utf-8"
            )
        )


def test_bridge_topology_available():

    topology = get_json(
        TOPOLOGY_URL
    )

    assert "nodes" in topology
    assert "edges" in topology

    assert (
        len(topology["nodes"])
        > 0
    )

    assert (
        len(topology["edges"])
        > 0
    )


def test_network_device_inventory_available():

    devices = get_json(
        DEVICES_URL
    )

    assert isinstance(
        devices,
        list,
    )

    names = {
        device.get("text")
        for device in devices
    }

    assert "R6" in names
    assert "R7" in names
    assert "R8" in names
    assert "S5" in names


def test_r6_management_reachable():

    devices = get_json(
        DEVICES_URL
    )

    r6 = next(
        device
        for device in devices
        if device.get("text")
        == "R6"
    )

    mgmt_ip = r6["value"]

    result = subprocess.run(
        [
            "ping",
            "-c",
            "1",
            "-W",
            "2",
            mgmt_ip,
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert (
        result.returncode
        == 0
    )


def test_r6_r7_link_exists():

    topology = get_json(
        TOPOLOGY_URL
    )

    matching = [
        edge
        for edge
        in topology["edges"]
        if {
            edge.get("source"),
            edge.get("target"),
        }
        == {
            "R6",
            "R7",
        }
    ]

    assert len(matching) == 1

    edge = matching[0]

    interfaces = {
        edge.get(
            "source_interface"
        ),
        edge.get(
            "target_interface"
        ),
    }

    assert "Ethernet2" in interfaces
    assert "Ethernet1" in interfaces


def test_self_healing_wrapper_runs():

    result = subprocess.run(
        [
            "sudo",
            "-n",
            SELF_HEALING,
            "--once",
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )

    assert (
        result.returncode
        == 0
    )

    assert (
        "[Self-Healing]"
        in result.stdout
    )


def test_healthy_network_has_no_actionable_incident():

    result = subprocess.run(
        [
            "sudo",
            "-n",
            SELF_HEALING,
            "--once",
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )

    assert (
        result.returncode
        == 0
    )

    assert (
        "0 actionable incident(s)"
        in result.stdout
    )
