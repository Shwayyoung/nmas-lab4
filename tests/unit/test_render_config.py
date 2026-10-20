import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


PROJECT = Path(__file__).resolve().parents[2]


@pytest.fixture
def render_lab(tmp_path):
    (tmp_path / "automation").mkdir()
    (tmp_path / "inventory" / "host_vars").mkdir(parents=True)

    shutil.copy2(
        PROJECT / "automation" / "render_config.py",
        tmp_path / "automation" / "render_config.py",
    )
    shutil.copytree(
        PROJECT / "templates",
        tmp_path / "templates",
    )

    return tmp_path


def run_render(base, device):
    return subprocess.run(
        [
            sys.executable,
            str(base / "automation" / "render_config.py"),
            device,
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.mark.parametrize("device", ["R1", "R3"])
def test_render_real_yaml(render_lab, device):
    source = PROJECT / "inventory" / "host_vars" / f"{device}.yml"
    target = render_lab / "inventory" / "host_vars" / f"{device}.yml"
    shutil.copy2(source, target)

    result = run_render(render_lab, device)
    output = render_lab / "configs" / "generated" / f"{device}.cfg"

    assert result.returncode == 0, result.stderr
    assert output.is_file()
    assert output.stat().st_size > 0
    assert output.read_text().endswith("\n")
    assert f"Generated: {output}" in result.stdout


def test_unsupported_platform(render_lab):
    yaml_file = render_lab / "inventory" / "host_vars" / "TEST.yml"
    yaml_file.write_text(
        yaml.safe_dump({"vendor": "cisco", "platform": "ios"})
    )

    result = run_render(render_lab, "TEST")

    assert result.returncode != 0
    assert "Unsupported platform: cisco/ios" in result.stderr
    assert not (render_lab / "configs" / "generated" / "TEST.cfg").exists()


def test_missing_template_variable(render_lab):
    yaml_file = render_lab / "inventory" / "host_vars" / "TEST.yml"
    yaml_file.write_text(
        yaml.safe_dump({"vendor": "arista", "platform": "ceos"})
    )

    result = run_render(render_lab, "TEST")

    assert result.returncode != 0
    assert "UndefinedError" in result.stderr
    assert not (render_lab / "configs" / "generated" / "TEST.cfg").exists()


def test_missing_yaml(render_lab):
    result = run_render(render_lab, "NONEXISTENT")

    assert result.returncode != 0
    assert "FileNotFoundError" in result.stderr
    assert not (
        render_lab / "configs" / "generated" / "NONEXISTENT.cfg"
    ).exists()
