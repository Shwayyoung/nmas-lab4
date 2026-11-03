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

def execute_day2(apply, retry):
    fingerprint = deployment_fingerprint()
    previous = load_state()

    same_deployment = previous.get("fingerprint") == fingerprint
    status = previous.get("status") if same_deployment else None

    if apply:
        if status == "SUCCESS":
            print("[Full-ZTP] Day-2 already SUCCESS; skipping")
            return

        if status in ("FAILED", "RUNNING") and not retry:
            print(
                f"[Full-ZTP] Previous Day-2 state: {status}. "
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
        })

    print(
        "[Full-ZTP] Starting Day-2 "
        + ("APPLY" if apply else "DRY RUN"),
        flush=True,
    )

    try:
        result = subprocess.run(command, cwd=ROOT)
        code = result.returncode
    except Exception:
        if apply:
            state = load_state()
            state.update({
                "status": "FAILED",
                "finished_at": timestamp(),
                "exit_code": None,
            })
            save_state(state)
        raise

    if apply:
        state = load_state()
        state.update({
            "status": "SUCCESS" if code == 0 else "FAILED",
            "finished_at": timestamp(),
            "exit_code": code,
        })
        save_state(state)

    if code:
        print(f"[Full-ZTP] Day-2 FAILED: exit={code}")
    else:
        print("[Full-ZTP] Day-2 completed successfully")

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
