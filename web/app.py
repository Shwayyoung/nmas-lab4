from flask import Flask, render_template, request, redirect, url_for, flash, abort
from pathlib import Path
import subprocess
import yaml
import re
import sys
import json

app = Flask(__name__)
app.secret_key = "nmas-lab4-dev-key"

BASE = Path(__file__).resolve().parent.parent
HOST_VARS = BASE / "inventory" / "host_vars"
RENDER_SCRIPT = BASE / "automation" / "render_config.py"
GENERATED_DIR = BASE / "configs" / "generated"
GOLDEN_SCRIPT = BASE / "automation" / "save_golden.py"
GOLDEN_DIR = BASE / "golden"
HEALTH_REPORT = Path(
    "/home/student/labs/nmas-lab4/reports/health/health-latest.json"
)
CONFIG_CHANGE_DIR = Path(
    "/home/student/labs/nmas-lab4/reports/config_changes"
)

def valid_hostname(name):
    return bool(re.fullmatch(r"[A-Za-z0-9_-]+", name))

def load_device_yaml(device):
    if not valid_hostname(device):
        abort(400)

    yaml_file = HOST_VARS / f"{device}.yml"

    if not yaml_file.exists():
        abort(404)

    with open(yaml_file) as f:
        return yaml.safe_load(f), yaml_file

def clean_list(values):
    return [v.strip() for v in values if v.strip()]


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/devices/new", methods=["GET", "POST"])
def new_device():
    if request.method == "POST":
        hostname = request.form["hostname"].strip()
        vendor = request.form["vendor"].strip()
        platform = request.form["platform"].strip()
        management_ip = request.form["management_ip"].strip()
        routing_protocol = request.form["routing_protocol"].strip()
        router_id = request.form["router_id"].strip()
        bgp_as = request.form["bgp_as"].strip()

        if not valid_hostname(hostname):
            flash("Invalid hostname.", "danger")
            return redirect(url_for("new_device"))

        yaml_file = HOST_VARS / f"{hostname}.yml"

        if yaml_file.exists():
            flash(f"{hostname}.yml already exists.", "danger")
            return redirect(url_for("new_device"))

        interface_names = request.form.getlist("interface_name[]")
        interface_ipv4s = request.form.getlist("interface_ipv4[]")
        interface_ipv6s = request.form.getlist("interface_ipv6[]")

        interfaces = []

        for name, ipv4, ipv6 in zip(
            interface_names,
            interface_ipv4s,
            interface_ipv6s,
        ):
            name = name.strip()
            ipv4 = ipv4.strip()
            ipv6 = ipv6.strip()

            if not name:
                continue

            interface = {"name": name}

            if ipv4:
                interface["ipv4"] = ipv4

            if ipv6:
                interface["ipv6"] = ipv6

            if vendor == "arista":
                interface["mode"] = "routed"

            interfaces.append(interface)

        loopback_ipv4 = request.form["loopback_ipv4"].strip()
        loopback_ipv6 = request.form["loopback_ipv6"].strip()

        data = {
            "hostname": hostname,
            "vendor": vendor,
            "platform": platform,
            "management": {
                "address": management_ip
            },
            "interfaces": interfaces,
        }

        if loopback_ipv4 or loopback_ipv6:
            data["loopback"] = {}

            if loopback_ipv4:
                data["loopback"]["ipv4"] = loopback_ipv4

            if loopback_ipv6:
                data["loopback"]["ipv6"] = loopback_ipv6

        # ---------------------------------------------------------
        # Routing protocol
        # ---------------------------------------------------------

        if routing_protocol == "ospf":

            if not router_id:
                flash("Router ID is required for OSPF.", "danger")
                return redirect(url_for("new_device"))

            if vendor == "arista":

                data["routing"] = {
                    "router_id": router_id,
                    "ospf": {
                        "process": 1,
                        "max_lsa": 12000,
                    }
                }

                # Put routed interfaces into OSPF area 0
                for interface in data["interfaces"]:
                    if "ipv4" in interface:
                        interface["ospf_area"] = "0.0.0.0"

                    if "ipv6" in interface:
                        interface["ospfv3_process"] = 1
                        interface["ospfv3_area"] = "0.0.0.0"

                if "loopback" in data:
                    data["loopback"]["ospf_area"] = "0.0.0.0"
                    data["loopback"]["ospfv3_process"] = 1
                    data["loopback"]["ospfv3_area"] = "0.0.0.0"

            elif vendor == "nokia":

                ospf_interfaces = [
                    f"{interface['name']}.0"
                    for interface in interfaces
                ]

                if "loopback" in data:
                    ospf_interfaces.append("lo0.0")

                data["ospf"] = {
                    "router_id": router_id,
                    "interfaces": ospf_interfaces,
                    "ipv4_export_policy": "WEB-IPV4-IMPORT",
                    "ipv6_export_policy": "WEB-IPV6-IMPORT",
                }

        elif routing_protocol == "bgp":

            if not router_id:
                flash("Router ID is required for BGP.", "danger")
                return redirect(url_for("new_device"))

            if not bgp_as:
                flash("BGP Local AS is required.", "danger")
                return redirect(url_for("new_device"))

            neighbor_addresses = request.form.getlist(
                "neighbor_address[]"
            )

            neighbor_as_numbers = request.form.getlist(
                "neighbor_as[]"
            )

            neighbors = []

            for address, peer_as in zip(
                neighbor_addresses,
                neighbor_as_numbers,
            ):
                address = address.strip()
                peer_as = peer_as.strip()

                if not address:
                    continue

                if not peer_as:
                    flash(
                        f"Peer AS missing for neighbor {address}.",
                        "danger",
                    )
                    return redirect(url_for("new_device"))

                neighbors.append({
                    "address": address,
                    "peer_as": int(peer_as),
                })

            data["bgp"] = {
                "local_as": int(bgp_as),
                "router_id": router_id,
                "group": "EBGP",
                "neighbors": neighbors,
            }

            if vendor == "nokia":
                data["bgp"].update({
                    "ipv4_export_policy": "WEB-IPV4-EXPORT",
                    "ipv4_import_policy": "HOST-IPV4-IMPORT",
                    "ipv6_export_policy": "WEB-IPV6-EXPORT",
                    "ipv6_import_policy": "HOST-IPV6-IMPORT",
                })

        # ---------------------------------------------------------
        # Nokia routing-policy defaults
        # ---------------------------------------------------------

        if vendor == "nokia":

            data["prefix_sets"] = {
                "HOST-IPV4": [
                    "192.168.10.0/24",
                    "192.168.20.0/24",
                ],
                "HOST-IPV6": [
                    "2001:db8:5840:10::/64",
                    "2001:db8:5840:20::/64",
                    "2001:db8:5840:30::/64",
                ],
                "WEB-IPV4": [
                    "192.168.100.0/24"
                ],
                "WEB-IPV6": [
                    "2001:db8:5840:100::/64"
                ],
            }

        # ---------------------------------------------------------
        # Save YAML
        # ---------------------------------------------------------

        HOST_VARS.mkdir(parents=True, exist_ok=True)

        with open(yaml_file, "w") as f:
            yaml.safe_dump(
                data,
                f,
                sort_keys=False,
                default_flow_style=False,
            )

        # ---------------------------------------------------------
        # Render configuration automatically
        # ---------------------------------------------------------

        result = subprocess.run(
            [
                sys.executable,
                str(RENDER_SCRIPT),
                hostname,
            ],
            cwd=BASE,
            text=True,
            capture_output=True,
        )

        if result.returncode != 0:
            flash(
                f"YAML created, but config rendering failed: "
                f"{result.stderr}",
                "warning",
            )
            return redirect(url_for("new_device"))

        flash(
            f"{hostname} created successfully. "
            f"YAML and generated configuration are ready.",
            "success",
        )

        return redirect(url_for("new_device"))

    return render_template("new_device.html")

@app.route("/devices")
def devices():
    device_list = []

    for yaml_file in sorted(HOST_VARS.glob("*.yml")):
        try:
            with open(yaml_file) as f:
                data = yaml.safe_load(f) or {}

            device_list.append({
                "hostname": data.get("hostname", yaml_file.stem),
                "vendor": data.get("vendor", "unknown"),
                "platform": data.get("platform", "unknown"),
            })

        except Exception:
            device_list.append({
                "hostname": yaml_file.stem,
                "vendor": "ERROR",
                "platform": "ERROR",
            })

    return render_template(
        "devices.html",
        devices=device_list,
    )


@app.route("/devices/<device>/yaml")
def view_yaml(device):
    data, yaml_file = load_device_yaml(device)

    yaml_text = yaml.safe_dump(
        data,
        sort_keys=False,
        default_flow_style=False,
    )

    return render_template(
        "view_yaml.html",
        device=device,
        yaml_text=yaml_text,
    )


@app.route("/devices/<device>/render", methods=["POST"])
def render_device(device):
    load_device_yaml(device)

    result = subprocess.run(
        [
            sys.executable,
            str(RENDER_SCRIPT),
            device,
        ],
        cwd=BASE,
        text=True,
        capture_output=True,
    )

    if result.returncode == 0:
        flash(
            f"{device} configuration generated successfully.",
            "success",
        )
    else:
        flash(
            f"{device} rendering failed: {result.stderr}",
            "danger",
        )

    return redirect(url_for("devices"))


@app.route("/devices/<device>/golden", methods=["POST"])
def golden_device(device):
    load_device_yaml(device)

    result = subprocess.run(
        [
            sys.executable,
            str(GOLDEN_SCRIPT),
            device,
        ],
        cwd=BASE,
        text=True,
        capture_output=True,
    )

    if result.returncode == 0:
        flash(
            f"{device} Golden Configuration saved.",
            "success",
        )
    else:
        flash(
            f"{device} Golden save failed: {result.stderr}",
            "danger",
        )

    return redirect(url_for("devices"))

@app.route("/devices/<device>/generated")
def view_generated(device):
    load_device_yaml(device)

    config_file = GENERATED_DIR / f"{device}.cfg"

    if not config_file.exists():
        flash(
            f"No generated configuration found for {device}.",
            "warning",
        )
        return redirect(url_for("devices"))

    config_text = config_file.read_text()

    return render_template(
        "view_config.html",
        device=device,
        title=f"{device} Generated Configuration",
        config_text=config_text,
    )


@app.route("/devices/<device>/golden/history")
def golden_history(device):
    load_device_yaml(device)

    device_dir = GOLDEN_DIR / device
    files = []

    if device_dir.exists():
        for config_file in sorted(
            device_dir.glob("*.cfg"),
            reverse=True,
        ):
            files.append({
                "name": config_file.name,
            })

    return render_template(
        "golden_history.html",
        device=device,
        files=files,
    )


@app.route("/devices/<device>/golden/<filename>")
def view_golden(device, filename):
    load_device_yaml(device)

    # Prevent path traversal
    if Path(filename).name != filename:
        abort(400)

    config_file = GOLDEN_DIR / device / filename

    if not config_file.exists():
        abort(404)

    config_text = config_file.read_text()

    return render_template(
        "view_config.html",
        device=device,
        title=f"{device} Golden Configuration",
        config_text=config_text,
    )

@app.route("/monitoring")
def monitoring():
    return render_template("monitoring.html")

@app.route("/topology")
def topology():
    return render_template("topology.html")


@app.route("/api/topology")
def topology_api():
    import urllib.request
    import json

    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:8088/api/topology",
            timeout=20,
        ) as response:

            data = json.loads(
                response.read().decode("utf-8")
            )

        return data

    except Exception as exc:
        return {
            "error": str(exc)
        }, 500

@app.route("/health")
def health_dashboard():

    results = []

    if HEALTH_REPORT.exists():

        with HEALTH_REPORT.open() as f:
            results = json.load(f)

    return render_template(
        "health.html",
        results=results,
    )


@app.route("/health/run", methods=["POST"])
def run_health_check():

    subprocess.run(
        [
            "/usr/bin/python3",
            "/home/student/labs/nmas-lab4/automation/health_check.py",
            "--all",
        ],
        cwd="/home/student/labs/nmas-lab4",
        check=False,
    )

    return redirect(
        url_for("health_dashboard")
    )

@app.route("/config-changes")
def config_changes():

    reports = []

    if CONFIG_CHANGE_DIR.exists():

        for path in sorted(
            CONFIG_CHANGE_DIR.glob("*-latest.json")
        ):

            with path.open() as f:
                reports.append(
                    json.load(f)
                )

    return render_template(
        "config_changes.html",
        reports=reports,
    )


@app.route(
    "/config-changes/run",
    methods=["POST"],
)
def run_config_changes():

    subprocess.run(
        [
            "/usr/bin/python3",
            "/home/student/labs/nmas-lab4/automation/config_change.py",
            "--all",
        ],
        cwd="/home/student/labs/nmas-lab4",
        check=False,
    )

    return redirect(
        url_for("config_changes")
    )

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True,
    )