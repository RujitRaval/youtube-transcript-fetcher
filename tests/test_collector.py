from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import pytest

from youtube_transcript_collector import collector as module
from youtube_transcript_collector.collector import Collector
from youtube_transcript_collector.output.storage import relative_path, save
from youtube_transcript_collector.retry import RetrievalError
from youtube_transcript_collector.utils import iso, utcnow


def test_ten_runs_fetch_once(config, db, video, transcript, monkeypatch):
    monkeypatch.setattr(module, "discover", Mock(return_value=[video]))
    fetch = Mock(return_value=transcript)
    monkeypatch.setattr(module, "retrieve", fetch)
    for _ in range(10):
        Collector(config, db).run()
    assert fetch.call_count == 1
    assert db.counts() == {"complete": 1}
    assert len(list(config.output_directory.rglob("metadata.json"))) == 1


def test_backoff_exhaustion_and_reset(config, db, video, monkeypatch):
    db.insert(video)
    fetch = Mock(side_effect=RetrievalError("transcript_unavailable", "not ready"))
    monkeypatch.setattr(module, "retrieve", fetch)
    monkeypatch.setattr(module, "discover", Mock(return_value=[]))
    collector = Collector(config, db)
    collector.run()
    row = db.get(video.video_id)
    assert row["status"] == "waiting_for_transcript" and row["attempt_count"] == 1
    collector.run()
    assert fetch.call_count == 1
    for _ in range(5):
        db.update(video.video_id, next_attempt_at=iso(utcnow() - timedelta(seconds=1)))
        collector.run()
    assert db.get(video.video_id)["status"] == "failed"
    assert fetch.call_count == 6
    assert db.retry_failed() == 1
    assert db.get(video.video_id)["attempt_count"] == 0


def test_rate_limit_stops_cycle_and_persists(config, db, video, monkeypatch):
    other = replace(video, video_id="12345678901")
    monkeypatch.setattr(module, "discover", Mock(return_value=[video, other]))
    fetch = Mock(side_effect=RetrievalError("rate_limited", "429"))
    monkeypatch.setattr(module, "retrieve", fetch)
    Collector(config, db).run()
    assert fetch.call_count == 1
    assert db.setting("cooldown_until")
    discover = Mock(side_effect=AssertionError("should not discover"))
    monkeypatch.setattr(module, "discover", discover)
    Collector(config, db).run()
    discover.assert_not_called()
    assert fetch.call_count == 1


def test_discovery_failure_still_retries_known_videos(config, db, video, transcript, monkeypatch):
    db.insert(video)
    monkeypatch.setattr(module, "discover", Mock(side_effect=ConnectionError("network outage")))
    monkeypatch.setattr(module, "retrieve", Mock(return_value=transcript))
    collector = Collector(config, db)
    collector.run()
    assert collector.had_errors and db.get(video.video_id)["status"] == "complete"


def test_crash_after_save_recovers_without_fetch(config, db, video, transcript, monkeypatch):
    db.insert(video)
    db.update(video.video_id, status="processing", attempt_count=1, operation_id="operation")
    row = db.get(video.video_id)
    relative = str(relative_path(row))
    db.update(video.video_id, output_path=relative)
    save(config.output_directory, relative, row, transcript)
    fetch = Mock(side_effect=AssertionError("must recover"))
    monkeypatch.setattr(module, "retrieve", fetch)
    Collector(config, db).process(db.get(video.video_id))
    fetch.assert_not_called()
    assert db.get(video.video_id)["status"] == "complete"


def test_crash_before_save_waits_and_exhausts(config, db, video, monkeypatch):
    db.insert(video)
    db.update(video.video_id, status="processing", attempt_count=6)
    fetch = Mock()
    monkeypatch.setattr(module, "retrieve", fetch)
    Collector(config, db).process(db.get(video.video_id))
    assert db.get(video.video_id)["status"] == "failed"
    assert db.get(video.video_id)["error_kind"] == "interrupted"
    fetch.assert_not_called()


def test_force_uses_same_path_and_does_not_recover_old_bundle(
    config, db, video, transcript, monkeypatch
):
    db.insert(video)
    fetch = Mock(return_value=transcript)
    monkeypatch.setattr(module, "retrieve", fetch)
    collector = Collector(config, db)
    collector.process(db.get(video.video_id))
    original_path = db.get(video.video_id)["output_path"]
    db.update(
        video.video_id,
        status="discovered",
        attempt_count=0,
        refresh_requested=1,
        operation_id=None,
        title="new title",
    )
    collector.process(db.get(video.video_id))
    assert fetch.call_count == 2
    assert db.get(video.video_id)["output_path"] == original_path
    assert len(list(config.output_directory.rglob("metadata.json"))) == 1


@pytest.mark.parametrize(
    "kind,status",
    [
        ("network", "waiting_for_transcript"),
        ("unavailable", "failed"),
        ("unexpected", "waiting_for_transcript"),
    ],
)
def test_error_transitions(config, db, video, monkeypatch, kind, status):
    db.insert(video)
    monkeypatch.setattr(module, "retrieve", Mock(side_effect=RetrievalError(kind, "test")))
    Collector(config, db).process(db.get(video.video_id))
    row = db.get(video.video_id)
    assert row["status"] == status and row["error_kind"] == kind


def test_forced_refresh_crash_after_save_recovers(config, db, video, transcript, monkeypatch):
    db.insert(video)
    row = db.get(video.video_id)
    relative = str(relative_path(row))
    db.update(
        video.video_id,
        status="processing",
        output_path=relative,
        attempt_count=1,
        refresh_requested=1,
        operation_id="new-operation",
    )
    save(config.output_directory, relative, db.get(video.video_id), transcript)
    fetch = Mock(side_effect=AssertionError("must recover forced write"))
    monkeypatch.setattr(module, "retrieve", fetch)
    Collector(config, db).process(db.get(video.video_id))
    assert db.get(video.video_id)["status"] == "complete"
    assert db.get(video.video_id)["refresh_requested"] == 0
    fetch.assert_not_called()


def test_metadata_failure_does_not_discard_captions(config, db, video, transcript, monkeypatch):
    video.published_at = None
    db.insert(video)
    monkeypatch.setattr(module, "retrieve", Mock(return_value=transcript))
    monkeypatch.setattr(module, "details", Mock(side_effect=RetrievalError("rate_limited", "429")))
    collector = Collector(config, db)
    collector.process(db.get(video.video_id))
    assert db.get(video.video_id)["status"] == "complete"
    assert collector.cooling_down()


def test_run_respects_processing_limit(config, db, video, transcript, monkeypatch):
    config = replace(config, max_videos_per_run=1)
    monkeypatch.setattr(
        module, "discover", Mock(return_value=[video, replace(video, video_id="12345678901")])
    )
    fetch = Mock(return_value=transcript)
    monkeypatch.setattr(module, "retrieve", fetch)
    Collector(config, db).run()
    assert db.counts() == {"complete": 1, "discovered": 1}
    assert fetch.call_count == 1
