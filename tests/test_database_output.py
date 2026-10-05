import json

import pytest

from youtube_transcript_collector.database import Database, writer_lock
from youtube_transcript_collector.output import markdown, storage, vtt
from youtube_transcript_collector.utils import utcnow


def test_init_duplicates_and_transitions(db, video):
    db.insert(video)
    db.insert(video)
    assert db.counts() == {"discovered": 1}
    db.update(video.video_id, status="processing", attempt_count=1)
    assert db.get(video.video_id)["attempt_count"] == 1
    db.update(video.video_id, status="complete")
    assert db.due(utcnow(), 10) == []
    db.insert(video)
    assert db.counts() == {"complete": 1}


def test_lock_and_release(config):
    with writer_lock(config.database):
        with pytest.raises(RuntimeError, match="Another collector"):
            with writer_lock(config.database):
                pass
    with writer_lock(config.database):
        pass


def test_database_reopen(config, video):
    with Database(config.database) as db:
        db.insert(video)
    with Database(config.database) as db:
        assert db.counts() == {"discovered": 1}
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 1


def test_future_schema_rejected(config):
    with Database(config.database) as db:
        db.connection.execute("PRAGMA user_version=99")
    with pytest.raises(RuntimeError, match="newer"):
        Database(config.database)


def test_output_bundle(config, db, video, transcript):
    db.insert(video)
    row = db.get(video.video_id)
    relative = storage.relative_path(row)
    metadata = storage.save(config.output_directory, relative, row, transcript)
    target = config.output_directory / relative
    assert video.video_id in str(relative)
    assert json.loads((target / "transcript.raw.json").read_text()) == transcript.segments
    md = (target / "transcript.md").read_text()
    assert "**[17:18]**" in md and "café" in md and "**Duration:** 01:23:41" in md
    assert "<b>" not in md
    assert "00:17:18.100 --> 00:17:24.300" in (target / "transcript.vtt").read_text()
    assert storage.recover(config.output_directory, relative, video.video_id) == metadata
    (target / "transcript.raw.json").write_text("broken")
    assert storage.recover(config.output_directory, relative, video.video_id) is None


def test_atomic_write_preserves_old_file_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "file.txt"
    target.write_text("old")

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(storage.os, "replace", fail)
    with pytest.raises(OSError):
        storage.atomic_write(target, "new")
    assert target.read_text() == "old"
    assert list(tmp_path.iterdir()) == [target]


def test_output_path_cannot_escape(tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        storage.target_path(tmp_path, "../outside")


def test_vtt_escaping_and_zero_duration():
    result = vtt.render([{"start": 1, "duration": 0, "text": "A --> B\n\nC & D"}])
    assert "00:00:01.000 --> 00:00:01.001" in result
    assert "A --&gt; B\nC &amp; D" in result


def test_markdown_untrusted_text_is_escaped(db, video, transcript):
    video.title = "# [click](https://evil.test)"
    db.insert(video)
    assert "\\[click\\]" in markdown.render(db.get(video.video_id), transcript)


def test_unknown_publication_path(db, video):
    video.published_at = None
    db.insert(video)
    relative = storage.relative_path(db.get(video.video_id))
    assert relative.parent.name == "unknown-date"
    assert relative.name.startswith("unknown-date-")


@pytest.mark.parametrize(
    "metadata",
    [
        [],
        {"files": []},
        {"files": {}},
        {"video_id": "abcdefghijk", "files": {}, "collected_at": 12},
    ],
)
def test_invalid_recovery_manifest_is_ignored(tmp_path, metadata):
    target = tmp_path / "bundle"
    target.mkdir()
    (target / "metadata.json").write_text(json.dumps(metadata))
    assert storage.recover(tmp_path, "bundle", "abcdefghijk") is None
