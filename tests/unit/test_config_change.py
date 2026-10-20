from unittest.mock import patch

import pytest

from automation import config_change as cc


@pytest.mark.parametrize(
    "golden_text, generated_text, expected_changed",
    [
        ("hostname R1\n", "hostname R1\n", False),
        ("hostname R1\n", "hostname R2\n", True),
    ],
)
def test_compare_configs(
    tmp_path, golden_text, generated_text, expected_changed
):
    golden = tmp_path / "golden.cfg"
    generated = tmp_path / "generated.cfg"

    golden.write_text(golden_text)
    generated.write_text(generated_text)

    with patch.object(cc, "render_device", return_value=generated), \
         patch.object(cc, "get_golden_config", return_value=golden):
        result = cc.compare_configs("R1")

    assert result["device"] == "R1"
    assert result["changed"] is expected_changed
    assert result["golden"] == golden
    assert result["generated"] == generated

    if expected_changed:
        assert "--- R1-golden" in result["diff"]
        assert "+++ R1-desired" in result["diff"]
        assert "-hostname R1" in result["diff"]
        assert "+hostname R2" in result["diff"]
    else:
        assert result["diff"] == ""


def test_get_golden_config_success(tmp_path, monkeypatch):
    golden = tmp_path / "R1"
    golden.mkdir()
    cfg = golden / "R1-latest.cfg"
    cfg.write_text("hostname R1\n")

    monkeypatch.setattr(cc, "GOLDEN_DIR", tmp_path)

    assert cc.get_golden_config("R1") == cfg


def test_get_golden_config_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "GOLDEN_DIR", tmp_path)

    with pytest.raises(RuntimeError, match="Golden config not found"):
        cc.get_golden_config("R1")

from types import SimpleNamespace
import json


def test_get_device_names(tmp_path, monkeypatch):
    (tmp_path / "R3.yml").touch()
    (tmp_path / "R1.yml").touch()
    (tmp_path / "R1.yaml").touch()
    (tmp_path / "R2.yaml").touch()

    monkeypatch.setattr(cc, "HOST_VARS_DIR", tmp_path)

    assert cc.get_device_names() == ["R1", "R2", "R3"]


@pytest.mark.parametrize("returncode,create_file,error", [
    (0, True, None),
    (1, False, "Render failed"),
    (0, False, "Generated config not found"),
])
def test_render_device(
    tmp_path, monkeypatch, returncode, create_file, error
):
    monkeypatch.setattr(cc, "GENERATED_DIR", tmp_path)

    generated = tmp_path / "R1.cfg"
    if create_file:
        generated.write_text("hostname R1\n")

    completed = SimpleNamespace(
        returncode=returncode,
        stderr="Render failed",
        stdout="",
    )

    with patch.object(
        cc.subprocess, "run", return_value=completed
    ) as mock_run:
        if error:
            with pytest.raises(RuntimeError, match=error):
                cc.render_device("R1")
        else:
            assert cc.render_device("R1") == generated

    mock_run.assert_called_once_with(
        [cc.sys.executable, str(cc.RENDER_SCRIPT), "R1"],
        cwd=cc.BASE_DIR,
        text=True,
        capture_output=True,
    )


@pytest.mark.parametrize("changed,expected", [
    (False, "NO CONFIGURATION CHANGES"),
    (True, "CONFIGURATION CHANGE DETECTED"),
])
def test_show_result(changed, expected, capsys):
    result = {
        "device": "R1",
        "changed": changed,
        "diff": "-hostname R1\n+hostname R2\n",
    }

    cc.show_result(result)
    output = capsys.readouterr().out

    assert expected in output

    if changed:
        assert "-hostname R1" in output
        assert "+hostname R2" in output


def test_save_change_report(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "CHANGE_HISTORY_DIR", tmp_path)

    result = {
        "device": "R1",
        "changed": True,
        "golden": tmp_path / "golden.cfg",
        "generated": tmp_path / "generated.cfg",
        "diff": "-old\n+new\n",
    }

    saved = cc.save_change_report(result)
    latest = tmp_path / "R1-latest.json"

    assert saved.exists()
    assert latest.exists()

    report = json.loads(saved.read_text())
    latest_report = json.loads(latest.read_text())

    assert report == latest_report
    assert report["device"] == "R1"
    assert report["changed"] is True
    assert report["diff"] == "-old\n+new\n"
    assert report["golden"] == str(result["golden"])
    assert report["generated"] == str(result["generated"])
    assert report