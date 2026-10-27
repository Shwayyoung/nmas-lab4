#!/usr/bin/env python3

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent / "ztp_state.json"
LOCK = threading.RLock()

VALID_STATES = {
    "DISCOVERED",
    "READY",
    "PROVISIONING",
    "CONFIGURED",
    "FAILED",
}

def get_state(device):
    with LOCK:
        if not STATE_FILE.exists():
            return None
        data = json.loads(STATE_FILE.read_text())
        return data.get(device)


def set_state(device, status, message=""):
    if status not in VALID_STATES:
        raise ValueError(f"Invalid ZTP state: {status}")

    with LOCK:
        data = {}
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text())

        data[device] = {
            "status": status,
            "message": message,
            "updated_at": datetime.now(
                timezone.utc
            ).isoformat(),
        }

        # Atomic file replacement prevents partial JSON writes.
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=STATE_FILE.parent,
            prefix=".ztp-state-",
            suffix=".tmp",
            delete=False,
        ) as f:
            temp_path = Path(f.name)
            json.dump(data, f, indent=2)

        try:
            os.replace(temp_path, STATE_FILE)
        finally:
            temp_path.unlink(missing_ok=True)

        print(f"[ZTP] {device}: {status} - {message}", flush=True)


def set_configured(device, container_id, config_sha256):
    """Persist the identity of a successfully configured instance."""
    if not container_id or not config_sha256:
        raise ValueError("Container ID and SHA256 are required")

    with LOCK:
        data = {}
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text())

        data[device] = {
            "status": "CONFIGURED",
            "container_id": container_id,
            "config_sha256": config_sha256,
            "message": "Configuration applied and verified",
            "updated_at": datetime.now(
                timezone.utc
            ).isoformat(),
        }

        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=STATE_FILE.parent,
            prefix=".ztp-state-",
            suffix=".tmp",
            delete=False,
        ) as f:
            temp_path = Path(f.name)
            json.dump(data, f, indent=2)

        try:
            os.replace(temp_path, STATE_FILE)
        finally:
            temp_path.unlink(missing_ok=True)


def is_configured(device, container_id, config_sha256):
    """Check whether this exact container/config pair was provisioned."""
    state = get_state(device)

    return bool(
        state
        and state.get("status") == "CONFIGURED"
        and state.get("container_id") == container_id
        and state.get("config_sha256") == config_sha256
    )
