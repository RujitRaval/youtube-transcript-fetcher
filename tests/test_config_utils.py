from datetime import UTC, datetime, timedelta

import pytest
import yaml

from youtube_transcript_collector.config import ConfigError, load_config
from youtube_transcript_collector.retry import classify, next_retry
from youtube_transcript_collector.utils import channel_folder, slug, timestamp, video_id


def write(tmp_path, data):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_defaults_relative_paths_and_channels(tmp_path, monkeypatch):
    path = write(tmp_path, {"channels": [{"name": "A", "url": "https://youtube.com/@a"}]})
    monkeypatch.chdir("/")
    config = load_config(path)
    assert config.database == tmp_path / "data/collector.db"
    assert config.recent_video_limit == 10
    assert config.channels[0].name == "A"


@pytest.mark.parametrize(
    "data,match",
    [
        ({}, "channels"),
        ({"channels": [], "unknown": 1}, "Unknown"),
        ({"channels": [], "collector": {"recent_video_limit": True}}, "number"),
        ({"channels": [], "collector": {"network_timeout_seconds": float("nan")}}, "between"),
        ({"channels": [], "transcripts": {"preferred_languages": "en"}}, "list"),
        ({"channels": [], "transcripts": {"retry": {"backoff_hours": []}}}, "nonempty"),
        ({"channels": [], "local_transcription": {"enabled": "false"}}, "true or false"),
        ({"channels": [], "local_transcription": {"enabled": True}}, "no V1 backend"),
        ({"channels": [{"name": "x", "url": "https://youtube.com.evil.test/@x"}]}, "HTTPS"),
        (
            {"channels": [{"name": "x", "url": "https://youtube.com/watch?v=abcdefghijk"}]},
            "channel",
        ),
        ({"channels": [], "storage": {"database": 12}}, "path"),
        ({"channels": [{"name": "a", "url": "https://youtube.com/@a"}] * 2}, "Duplicate"),
    ],
)
def test_invalid_config(tmp_path, data, match):
    with pytest.raises(ConfigError, match=match):
        load_config(write(tmp_path, data))


def test_malformed_and_missing_config(tmp_path):
    with pytest.raises(ConfigError, match="Cannot read config"):
        load_config(tmp_path / "missing")
    path = tmp_path / "bad.yaml"
    path.write_text("channels: [")
    with pytest.raises(ConfigError, match="Cannot read config"):
        load_config(path)


def test_slug_and_timestamps():
    assert slug("../../Café: A? / test") == "cafe-a-test"
    assert slug("🎧") == "untitled"
    assert channel_folder("A!") != channel_folder("A?")
    assert timestamp(74) == "01:14"
    assert timestamp(5021) == "01:23:41"
    assert timestamp(3599.9996, vtt=True) == "01:00:00.000"
    assert timestamp(1038.1, vtt=True) == "00:17:18.100"


@pytest.mark.parametrize(
    "value",
    [
        "abcdefghijk",
        "https://youtu.be/abcdefghijk?t=3",
        "https://youtube.com/watch?v=abcdefghijk&list=x",
        "https://youtube.com/shorts/abcdefghijk",
    ],
)
def test_video_id(value):
    assert video_id(value) == "abcdefghijk"


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.test/watch?v=abcdefghijk",
        "../abc",
        "https://youtube.com@evil.test/watch?v=abcdefghijk",
    ],
)
def test_bad_video_id(value):
    with pytest.raises(ValueError):
        video_id(value)


def test_backoff(config):
    now = datetime(2026, 10, 5, tzinfo=UTC)
    for attempt, hours in enumerate([1, 3, 12, 24, 48], 1):
        assert next_retry(config, attempt, now, "transcript_unavailable") == now + timedelta(
            hours=hours
        )
    assert next_retry(config, 6, now, "network") is None
    assert next_retry(config, 1, now, "unavailable") is None
    assert next_retry(config, 1, now, "rate_limited") == now + timedelta(hours=6)


@pytest.mark.parametrize(
    "message,kind",
    [
        ("HTTP Error 429", "rate_limited"),
        ("Private video", "unavailable"),
        ("Connection timed out", "network"),
        ("oops", "unexpected"),
    ],
)
def test_error_classification(message, kind):
    assert classify(Exception(message)).kind == kind
