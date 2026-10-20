from types import SimpleNamespace
from unittest.mock import patch

import pytest

from automation import save_golden as sg


def test_render_config_success(tmp_path, monkeypatch):
    generated = tmp_path / "generated"
    generated.mkdir()
    cfg = generated / "R1.cfg"
    cfg.write_text("hostname R1\n")

    monkeypatch.setattr(sg, "GENERATED_DIR", generated)

    result = SimpleNamespace(returncode=0, stdout="OK", stderr="")

    with patch.object(sg.subprocess, "run", return_value=result) as mock_run:
        output = sg.render_config("R1")

    assert output == cfg
    mock_run.assert_called_once_with(
        [sg.sys.executable, str(sg.RENDER_SCRIPT), "R1"],
        cwd=sg.BASE,
        text=True,
        capture_output=True,
    )


def test_render_config_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(sg, "GENERATED_DIR", tmp_path)

    result = SimpleNamespace(
        returncode=1, stdout="", stderr="Render failed"
    )

    with patch.object(sg.subprocess, "run", return_value=result):
        with pytest.raises(SystemExit) as exc:
            sg.render_config("R1")

    assert exc.value.code == 1


def test_render_config_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sg, "GENERATED_DIR", tmp_path)

    result = SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch.object(sg.subprocess, "run", return_value=result):
        with pytest.raises(SystemExit) as exc:
            sg.render_config("R1")

    assert exc.value.code == 1


def test_save_golden(tmp_path, monkeypatch):
    source = tmp_path / "R1.cfg"
    source.write_text("hostname R1\n")

    golden = tmp_path / "golden"
    monkeypatch.setattr(sg, "GOLDEN_DIR", golden)

    timestamped, latest = sg.save_golden("R1", source)

    assert timestamped.exists()
    assert latest.exists()
    assert timestamped.read_text() == "hostname R1\n"
    assert latest.read_text() == "hostname R1\n"
    assert timestamped.name.startswith("R1-")
    assert latest.name == "R1-latest.cfg"

def test_save_golden_missing_source(tmp_path, monkeypatch):
    missing_file = tmp_path / "nonexistent.cfg"

    golden = tmp_path / "golden"
    monkeypatch.setattr(sg, "GOLDEN_DIR", golden)

    with pytest.raises(FileNotFoundError):
        sg.save_golden("R1", missing_file)

    latest = golden / "R1" / "R1-latest.cfg"
    assert not latest.exists()

def test_save_golden_updates_latest(tmp_path, monkeypatch):
    source = tmp_path / "R1.cfg"
    source.write_text("hostname R1-old\n")

    golden = tmp_path / "golden"
    monkeypatch.setattr(sg, "GOLDEN_DIR", golden)

    sg.save_golden("R1", source)

    source.write_text("hostname R1-new\n")
    sg.save_golden("R1", source)

    latest = golden / "R1" / "R1-latest.cfg"

    assert latest.read_text() == "hostname R1-new\n"