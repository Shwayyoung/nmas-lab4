import subprocess

import pytest


DEVICES = {
    "R1": "172.20.20.6",
    "R2": "172.20.20.4",
    "R3": "172.20.20.14",
    "R4": "172.20.20.12",
    "R5": "172.20.20.7",
    "S1": "172.20.20.2",
    "S2": "172.20.20.13",
    "S3": "172.20.20.3",
    "S4": "172.20.20.8",
}


@pytest.mark.integration
@pytest.mark.parametrize(
    "device, ip_address",
    DEVICES.items(),
    ids=DEVICES.keys(),
)
def test_device_ping(device, ip_address):
    result = subprocess.run(
        ["ping", "-c", "2", "-W", "2", ip_address],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, (
        f"{device} ({ip_address}) is unreachable.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
