import socket

import pytest

from youtube_transcript_collector.config import Channel, Config
from youtube_transcript_collector.database import Database
from youtube_transcript_collector.models import Transcript, Video


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Tests must not access the network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)


@pytest.fixture
def config(tmp_path):
    return Config(
        tmp_path / "transcripts",
        tmp_path / "data/collector.db",
        (Channel("Test channel", "https://www.youtube.com/@test"),),
        request_delay_seconds=0,
    )


@pytest.fixture
def db(config):
    with Database(config.database) as database:
        yield database


@pytest.fixture
def video():
    return Video(
        "abcdefghijk",
        "Test channel",
        "https://www.youtube.com/@test",
        "A / test: title",
        "https://www.youtube.com/watch?v=abcdefghijk",
        "2026-10-04",
        5021,
    )


@pytest.fixture
def transcript():
    return Transcript(
        [
            {"start": 0.0, "duration": 1.5, "text": "Hello & welcome"},
            {"start": 1038.1, "duration": 6.2, "text": "A <b>test</b> café"},
        ],
        "youtube-auto",
        "en",
        "test",
    )
