import importlib.util
import plistlib
from pathlib import Path
from unittest.mock import Mock

import yaml

from youtube_transcript_collector import collector
from youtube_transcript_collector.cli import main
from youtube_transcript_collector.database import Database

PROJECT = Path(__file__).resolve().parents[1]


def test_cli_commands(tmp_path, monkeypatch, transcript, capsys):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"channels": []}))
    assert main(["--config", str(path), "doctor"]) == 0
    assert "Output writable:" in capsys.readouterr().out
    fetch = Mock(return_value=transcript)
    monkeypatch.setattr(collector, "retrieve", fetch)
    monkeypatch.setattr(collector, "details", Mock(return_value={}))
    monkeypatch.setattr(collector.time, "sleep", Mock())
    for _ in range(2):
        assert main(["--config", str(path), "collect", "abcdefghijk"]) == 0
    assert fetch.call_count == 1
    assert main(["--config", str(path), "collect", "abcdefghijk", "--force"]) == 0
    assert fetch.call_count == 2
    assert main(["--config", str(path), "status", "--details"]) == 0
    assert "complete: 1" in capsys.readouterr().out
    assert main(["--config", str(path), "channels"]) == 0
    assert main(["--config", str(path)]) == 0
    assert main(["--config", str(path), "retry-failed"]) == 0
    assert main(["--config", str(tmp_path / "missing"), "run"]) == 2
    with Database(tmp_path / "data/collector.db") as db:
        assert db.counts() == {"complete": 1}


def test_scheduler_render_handles_spaces_and_xml(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "render_scheduler", PROJECT / "scripts/render_scheduler.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = tmp_path / "A & B config.yaml"
    config.write_text("channels: []")
    python = Path("/Users/a person/project/.venv/bin/python")
    output = tmp_path / "output"
    path = module.render(PROJECT, config, output, "launchd", python)
    data = plistlib.loads(path.read_bytes())
    assert data["ProgramArguments"][0] == str(python)
    assert data["ProgramArguments"][4] == str(config)
    assert data["StartInterval"] == 3600
    service = module.render(PROJECT, config, output, "systemd", python).read_text()
    assert '"/Users/a person/project/.venv/bin/python"' in service
    assert "@@" not in service
    assert "Persistent=true" in (output / "youtube-transcript-collector.timer").read_text()
