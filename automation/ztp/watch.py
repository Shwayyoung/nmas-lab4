#!/usr/bin/env python3
"""Discover cEOS devices and optionally provision them."""

import argparse
import json
import subprocess
import threading
import time

from bootstrap import wait_until_ready
from apply_config import apply
from provision import preflight
from state import get_state, set_state

DEVICES = {"R6", "R7", "R8", "S5"}
LAB = "5840-lab1"
PREFIX = f"clab-{LAB}-"

active = set()
lock = threading.Lock()


def process_device(device, auto):
    try:
        info = preflight(device)

        if not info["ready_for_deployment"]:
            return

        print(f"[ZTP] {device}: discovered", flush=True)

        if not wait_until_ready(device, retries=30, interval=10):
            set_state(device, "FAILED", "EOS readiness timeout")
            return

        if auto:
            print(f"[ZTP] {device}: auto-provisioning", flush=True)
            apply(device)
        else:
            current = get_state(device)
            if not current or current["status"] != "CONFIGURED":
                set_state(device, "READY", "Awaiting provisioning")
            print(f"[ZTP] {device}: ready (dry-run)", flush=True)

    except Exception as exc:
        print(f"[ZTP] {device}: ERROR: {exc}", flush=True)
        set_state(device, "FAILED", str(exc))

    finally:
        with lock:
            active.discard(device)


def schedule(device, auto):
    if device not in DEVICES:
        return

    with lock:
        if device in active:
            return
        active.add(device)

    threading.Thread(
        target=process_device,
        args=(device, auto),
        daemon=True
    ).start()


def initial_discovery(auto):
    result = subprocess.run(
        [
            "docker", "ps",
            "--filter", f"label=containerlab={LAB}",
            "--format", "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    for name in result.stdout.splitlines():
        if name.startswith(PREFIX):
            schedule(name[len(PREFIX):], auto)


def watch(auto):
    print(f"[ZTP] Controller started: {LAB}", flush=True)
    print(f"[ZTP] Auto-provisioning: {auto}", flush=True)

    while True:
        try:
            cmd = [
                "docker", "events",
                "--filter", "type=container",
                "--filter", "event=start",
                "--format", "{{json .}}",
            ]

            with subprocess.Popen(
                cmd, stdout=subprocess.PIPE,
                text=True, bufsize=1
            ) as proc:
                initial_discovery(auto)

                print("[ZTP] Watching Docker events...", flush=True)

                for line in proc.stdout:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    attrs = event.get("Actor", {}).get("Attributes", {})
                    name = attrs.get("name", "")

                    if name.startswith(PREFIX):
                        schedule(name[len(PREFIX):], auto)

        except KeyboardInterrupt:
            print("\n[ZTP] Controller stopped", flush=True)
            return
        except Exception as exc:
            print(f"[ZTP] Watch error: {exc}", flush=True)

        time.sleep(3)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--auto-provision",
        action="store_true",
        help="Enable actual configuration deployment",
    )
    args = parser.parse_args()
    watch(args.auto_provision)
