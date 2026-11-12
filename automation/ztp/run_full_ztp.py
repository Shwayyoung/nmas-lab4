#!/usr/bin/env python3
"""ZTP watcher with persistent Day-2 execution state."""

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WATCH = ROOT / "automation/ztp/watch.py"
DAY2 = ROOT / "automation/day2/configure_existing.py"
NETBOX = ROOT / "automation/netbox/register_ztp_devices.py"
ROTATE_PASSWORD = ROOT / "automation/rotate_password.py"
MONITORING_SYNC = ROOT / "automation/monitoring/sync_netbox_to_telegraf.py"

STATE_DIR = ROOT / "automation/day2/state"
STATE_FILE = STATE_DIR / "full_ztp.json"
LOCK_FILE = Path("/tmp/nmas-lab4-day2.lock")

DEVICES = ("R6", "R7", "R8", "S5")
DAY2_FILES = (
    ROOT / "automation/day2/configure_r1.py",
    ROOT / "automation/day2/configure_r2.py",
    ROOT / "automation/day2/configure_r4.py",
    ROOT / "automation/day2/configure_r5.py",
    ROOT / "automation/day2/configure_existing.py",
    ROOT / "automation/netbox/register_ztp_devices.py",
    ROOT / "automation/rotate_password.py",
    ROOT / "automation/monitoring/sync_netbox_to_telegraf.py",
)

def timestamp():
    return datetime.now(timezone.utc).isoformat()

def load_state():
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text())

def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    os.replace(temporary, STATE_FILE)

def deployment_fingerprint():
    deployment = {}

    for device in DEVICES:
        result = subprocess.run(
            [
                "docker", "inspect",
                f"clab-5840-lab1-{device}",
                "--format", "{{.Id}}",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )

        config = ROOT / "configs/generated" / f"{device}.cfg"
        if not config.is_file():
            raise RuntimeError(f"Missing generated config: {config}")

        deployment[device] = {
            "container_id": result.stdout.strip(),
            "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        }

    day2_hashes = {}
    for path in DAY2_FILES:
        if not path.is_file():
            raise RuntimeError(f"Missing Day-2 script: {path}")
        day2_hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()

    payload = json.dumps(
        {"deployment": deployment, "day2": day2_hashes},
        sort_keys=True,
    ).encode()

    return hashlib.sha256(payload).hexdigest()

def ztp_ready():
    result = subprocess.run(
        [
            sys.executable, "-c",
            (
                "import sys; "
                f"sys.path.insert(0, {str(DAY2.parent)!r}); "
                "from configure_existing import check_ztp; "
                "sys.exit(0 if check_ztp() else 1)"
            ),
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
    )
    return result.returncode == 0

def privileged_command(command):
    """
    Run a command with root privileges when needed.

    If Full-ZTP itself is already running as root, execute directly.
    Otherwise use sudo while preserving the NetBox environment variables
    required by the password-rotation and monitoring-sync scripts.
    """
    if os.geteuid() == 0:
        return command

    return [
        "sudo",
        "--preserve-env=NETBOX_URL,NETBOX_TOKEN",
        *command,
    ]


def run_step(label, command, *, privileged=False):
    """Run one post-ZTP step and raise immediately on failure."""
    if privileged:
        command = privileged_command(command)

    print(f"[Full-ZTP] Starting {label}", flush=True)

    result = subprocess.run(
        command,
        cwd=ROOT,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"{label} FAILED: exit={result.returncode}"
        )

    print(
        f"[Full-ZTP] {label} completed successfully",
        flush=True,
    )


def restart_and_verify_telegraf():
    """Restart Telegraf so it reloads /etc/nmas-secrets and generated config."""
    run_step(
        "Telegraf restart",
        ["systemctl", "restart", "telegraf"],
        privileged=True,
    )

    time.sleep(2)

    command = ["systemctl", "is-active", "--quiet", "telegraf"]
    if os.geteuid() != 0:
        command = ["sudo", *command]

    result = subprocess.run(
        command,
        cwd=ROOT,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Telegraf health check FAILED: service is not active"
        )

    print(
        "[Full-ZTP] Telegraf health check passed",
        flush=True,
    )


def execute_day2(apply, retry):
    fingerprint = deployment_fingerprint()
    previous = load_state()

    same_deployment = previous.get("fingerprint") == fingerprint
    status = previous.get("status") if same_deployment else None

    if apply:
        if status == "SUCCESS":
            print(
                "[Full-ZTP] Day-2 + NetBox + credentials + monitoring "
                "already SUCCESS; skipping"
            )
            return

        if status in ("FAILED", "RUNNING") and not retry:
            print(
                f"[Full-ZTP] Previous post-ZTP state: {status}. "
                "Use --retry-day2 to retry."
            )
            return

    command = [sys.executable, str(DAY2)]

    if apply:
        command.append("--apply")

        save_state({
            "fingerprint": fingerprint,
            "status": "RUNNING",
            "started_at": timestamp(),
            "finished_at": None,
            "exit_code": None,
            "failed_step": None,
        })

    print(
        "[Full-ZTP] Starting Day-2 "
        + ("APPLY" if apply else "DRY RUN"),
        flush=True,
    )

    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Day-2 FAILED: exit={result.returncode}"
            )

        print(
            "[Full-ZTP] Day-2 completed successfully",
            flush=True,
        )

        if not apply:
            return

        # ---------------------------------------------------------------
        # 1. Register/update ZTP devices in NetBox.
        # ---------------------------------------------------------------
        run_step(
            "NetBox registration",
            [sys.executable, str(NETBOX)],
        )

        # ---------------------------------------------------------------
        # 2. Fresh ContainerLab deployments may be back on vendor
        #    bootstrap passwords even while /etc/nmas-secrets still
        #    contains passwords from the previous deployment.
        #
        #    --bootstrap first tries the stored per-device secret and
        #    falls back to ARISTA_BOOTSTRAP_PASSWORD /
        #    NOKIA_BOOTSTRAP_PASSWORD only when required.
        # ---------------------------------------------------------------
        run_step(
            "Bootstrap password rotation",
            [
                sys.executable,
                str(ROTATE_PASSWORD),
                "--all",
                "--apply",
                "--bootstrap",
            ],
            privileged=True,
        )

        # ---------------------------------------------------------------
        # 3. Regenerate Telegraf inputs from NetBox.
        # ---------------------------------------------------------------
        run_step(
            "NetBox-to-Telegraf sync",
            [
                sys.executable,
                str(MONITORING_SYNC),
            ],
            privileged=True,
        )

        # ---------------------------------------------------------------
        # 4. Telegraf must restart after password rotation because
        #    systemd EnvironmentFile values are read when the process
        #    starts. Restart also loads the newly generated input file.
        # ---------------------------------------------------------------
        restart_and_verify_telegraf()

    except Exception as exc:
        if apply:
            state = load_state()
            state.update({
                "status": "FAILED",
                "finished_at": timestamp(),
                "exit_code": 1,
                "error": str(exc),
            })
            save_state(state)

        raise

    if apply:
        state = load_state()
        state.update({
            "status": "SUCCESS",
            "finished_at": timestamp(),
            "exit_code": 0,
            "error": None,
        })
        save_state(state)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply-day2", action="store_true")
    parser.add_argument("--retry-day2", action="store_true")
    args = parser.parse_args()

    if args.retry_day2 and not args.apply_day2:
        parser.error("--retry-day2 requires --apply-day2")

    STATE_DIR.mkdir(parents=True, exist_ok=True)

    with LOCK_FILE.open("w") as lock:
        try:
            fcntl.flock(
                lock.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            raise RuntimeError("Another Full-ZTP coordinator is running")

        watcher = subprocess.Popen(
            [
                sys.executable, "-u",
                str(WATCH), "--auto-provision",
            ],
            cwd=ROOT,
        )

        print(f"[Full-ZTP] Watcher PID: {watcher.pid}")
        day2_checked = False

        try:
            while True:
                if watcher.poll() is not None:
                    raise RuntimeError(
                        f"Watcher exited: {watcher.returncode}"
                    )

                if not day2_checked and ztp_ready():
                    day2_checked = True
                    execute_day2(args.apply_day2, args.retry_day2)

                time.sleep(5)

        except KeyboardInterrupt:
            print("\n[Full-ZTP] Stopping")

        finally:
            watcher.terminate()
            try:
                watcher.wait(timeout=10)
            except subprocess.TimeoutExpired:
                watcher.kill()
                watcher.wait()

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"[Full-ZTP] ERROR: {error}", file=sys.stderr)
        sys.exit(1)
