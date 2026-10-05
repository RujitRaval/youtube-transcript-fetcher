import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from youtube_transcript_collector.cli import execute, parser
from youtube_transcript_collector.models import Transcript
from youtube_transcript_collector.output import storage
from youtube_transcript_collector.speakers import backend, store


@pytest.fixture
def caption_bundle(config, db, video):
    db.insert(video)
    row = db.get(video.video_id)
    relative = storage.relative_path(row)
    captions = [
        {"start": 0.0, "duration": 2.0, "text": "Hello & welcome"},
        {"start": 2.0, "duration": 2.0, "text": "- Hi.\n- Hello."},
        {"start": 5.0, "duration": 2.0, "text": "Later words"},
    ]
    storage.save(
        config.output_directory, relative, row, Transcript(captions, "youtube-manual", "en", "test")
    )
    db.update(video.video_id, status="complete", output_path=str(relative))
    return config.output_directory / relative


@pytest.fixture
def fake_backend(monkeypatch):
    monkeypatch.setattr(backend, "check_models", Mock())
    monkeypatch.setattr(backend, "dependencies", Mock())

    def prepare(directory, identifier, **kwargs):
        target = directory / "audio.wav"
        target.write_bytes(b"test audio, never sent to the network")
        return target

    monkeypatch.setattr(backend, "prepare_audio", prepare)
    infer = Mock(
        return_value=(
            [
                {"start": 0.0, "end": 2.5, "cluster": 7},
                {"start": 2.5, "end": 4.0, "cluster": 2},
            ],
            {"audio_seconds": 4.0, "inference_seconds": 0.1},
        )
    )
    monkeypatch.setattr(backend, "infer", infer)
    return infer


@pytest.fixture
def speaker_run(config, video, caption_bundle, fake_backend):
    return store.create_run(config, video.video_id, models=Path("unused-models"))


def core_bytes(bundle):
    return {path.name: path.read_bytes() for path in bundle.iterdir() if path.is_file()}


def test_publish_run_preserves_captions_and_numbers_first_voice(
    config, video, caption_bundle, fake_backend
):
    original = core_bytes(caption_bundle)
    directory, base, state = store.create_run(config, video.video_id, models=Path("unused"))
    assert base["partial"] is True
    assert base["turns"][0]["speaker_id"] == "speaker_1"
    assert base["turns"][0]["cluster"] == 7
    assert base["turns"][1]["speaker_id"] == "speaker_2"
    assert state["names"] == {"speaker_1": "", "speaker_2": ""}
    assert [cue["source_cue_index"] for cue in base["captions"]] == [0, 1]
    assert (directory / "audio.wav").read_bytes().startswith(b"test audio")
    assert store.load_run(caption_bundle) == (directory, base, state)
    assert core_bytes(caption_bundle) == original
    assert not list(directory.parent.glob(".pending-*"))


@pytest.mark.parametrize("failure_stage", ["inference", "run_write", "publish_pointer"])
def test_failed_run_retains_current_run(
    config, video, caption_bundle, speaker_run, fake_backend, monkeypatch, failure_stage
):
    old_directory, old_base, old_state = speaker_run
    original = core_bytes(caption_bundle)
    original_write = store.atomic_write

    def fail_write(path, text):
        if path.name == {"run_write": "run.json", "publish_pointer": "current.json"}.get(
            failure_stage
        ):
            raise OSError("disk full")
        return original_write(path, text)

    monkeypatch.setattr(store, "atomic_write", fail_write)
    if failure_stage == "inference":
        fake_backend.side_effect = RuntimeError("model failed")
    with pytest.raises((OSError, RuntimeError)):
        store.create_run(config, video.video_id, models=Path("unused"))
    assert store.load_run(caption_bundle) == (old_directory, old_base, old_state)
    assert core_bytes(caption_bundle) == original
    assert not list(old_directory.parent.glob(".pending-*"))
    # A pointer write failure may leave a complete orphan; it must never activate it.
    for directory in old_directory.parent.iterdir():
        assert (directory / "run.json").is_file()
        assert (directory / "edits.json").is_file()


def test_name_and_caption_edits_are_revisioned_and_preserve_model_result(
    caption_bundle, speaker_run
):
    directory, base, state = speaker_run
    immutable = (directory / "run.json").read_bytes()
    original = core_bytes(caption_bundle)
    updated = store.apply_edit(
        caption_bundle,
        run_id=base["run_id"],
        revision=0,
        names={"speaker_1": "  Nikhil Kamath  "},
        overrides={"1": ["speaker_1", "speaker_2"]},
    )
    assert updated["revision"] == 1
    assert updated["names"]["speaker_1"] == "Nikhil Kamath"
    assert updated["history"][0]["changes"] == {
        "names": {"speaker_1": {"before": "", "after": "Nikhil Kamath"}},
        "overrides": {"1": {"before": None, "after": ["speaker_1", "speaker_2"]}},
    }
    assert list(store.rendered_cues(base, updated))[1][1] == "Nikhil Kamath / Speaker 2"
    assert store.load_run(caption_bundle)[2] == updated
    no_op = store.apply_edit(
        caption_bundle, run_id=base["run_id"], revision=1, names={"speaker_1": "Nikhil Kamath"}
    )
    assert no_op == updated
    cleared = store.apply_edit(
        caption_bundle, run_id=base["run_id"], revision=1, overrides={"1": None}
    )
    assert cleared["revision"] == 2
    assert cleared["overrides"] == {}
    assert "review required" in list(store.rendered_cues(base, cleared))[1][1]
    assert (directory / "run.json").read_bytes() == immutable
    assert core_bytes(caption_bundle) == original


@pytest.mark.parametrize(
    "edit",
    [
        {"names": {"speaker_9": "Nobody"}},
        {"names": {"speaker_1": "line\nbreak"}},
        {"names": {"speaker_1": "x" * 121}},
        {"names": {"speaker_1": 42}},
        {"overrides": {"999": ["speaker_1"]}},
        {"overrides": {"1": []}},
        {"overrides": {"1": ["speaker_1", "speaker_1"]}},
        {"overrides": {"1": ["speaker_9"]}},
        {"overrides": {"1": [["speaker_1"]]}},
        {"names": {"speaker_1": "Valid"}, "overrides": {"1": ["speaker_9"]}},
    ],
)
def test_invalid_edit_never_partially_saves(caption_bundle, speaker_run, edit):
    directory, base, state = speaker_run
    old_bytes = (directory / "edits.json").read_bytes()
    with pytest.raises(ValueError):
        store.apply_edit(caption_bundle, run_id=base["run_id"], revision=0, **edit)
    assert (directory / "edits.json").read_bytes() == old_bytes


def test_stale_revision_and_previous_run_cannot_overwrite_edits(
    config, video, caption_bundle, speaker_run
):
    _, base, _ = speaker_run
    store.apply_edit(caption_bundle, run_id=base["run_id"], revision=0, names={"speaker_1": "Sam"})
    for revision in (0, True):
        with pytest.raises(ValueError, match="reload"):
            store.apply_edit(caption_bundle, run_id=base["run_id"], revision=revision)
    store.create_run(config, video.video_id, models=Path("unused"))
    with pytest.raises(ValueError, match="reload"):
        store.apply_edit(caption_bundle, run_id=base["run_id"], revision=0)
    assert store.load_run(caption_bundle, base["run_id"])[2]["names"]["speaker_1"] == "Sam"


def test_stale_caption_hash_blocks_review_and_export(caption_bundle, speaker_run):
    (caption_bundle / "transcript.raw.json").write_text("[]")
    with pytest.raises(ValueError, match="Captions changed"):
        store.load_run(caption_bundle)
    with pytest.raises(ValueError, match="Captions changed"):
        store.export_run(caption_bundle)


def test_exports_include_manual_names_original_words_and_complete_audit(
    caption_bundle, speaker_run
):
    directory, base, _ = speaker_run
    original = core_bytes(caption_bundle)
    updated = store.apply_edit(
        caption_bundle,
        run_id=base["run_id"],
        revision=0,
        names={"speaker_1": "Nikhil <test> *name*"},
        overrides={"1": ["speaker_2"]},
    )
    assert store.export_run(caption_bundle) == directory
    exported = json.loads((directory / "transcript.speakers.json").read_text())
    assert exported == {"analysis": base, "edits": updated}
    markdown = (directory / "transcript.speakers.md").read_text()
    assert "Hello & welcome" in markdown
    assert r"Nikhil \<test\> \*name\*" in markdown
    assert "Unknown speaker" not in markdown
    vtt = (directory / "transcript.speakers.vtt").read_text()
    assert "<v Nikhil &lt;test&gt; *name*>Hello &amp; welcome</v>" in vtt
    assert "<v Speaker 2>- Hi.\n- Hello.</v>" in vtt
    assert core_bytes(caption_bundle) == original


def test_alignment_flags_mixed_silent_boundary_and_multiline_captions():
    cues = [
        {"start": 0, "duration": 2, "text": "One voice"},
        {"start": 2, "duration": 2, "text": "Two voices"},
        {"start": 4, "duration": 2, "text": "No speech"},
        {"start": 6, "duration": 2, "text": "- Hello\n- Hi"},
        {"start": 8, "duration": 2, "text": "Clip boundary"},
        {"start": 10, "duration": 2, "text": "Beyond sample"},
    ]
    untouched = copy.deepcopy(cues)
    turns = store.normalize_turns(
        [
            {"start": 0, "end": 3, "cluster": 9},
            {"start": 2, "end": 4, "cluster": 1},
            {"start": 6, "end": 9, "cluster": 9},
        ],
        9,
    )
    aligned = store.align_captions(cues, turns, 9)
    assert len(aligned) == 5
    assert aligned[0]["speaker_ids"] == ["speaker_1"]
    assert aligned[1]["review_reasons"] == ["multiple_voices"]
    assert aligned[1]["speaker_ids"] == []
    assert aligned[2]["review_reasons"] == ["insufficient_audio_match"]
    assert aligned[3]["review_reasons"] == ["multiple_caption_lines"]
    assert aligned[4]["review_reasons"] == ["audio_boundary"]
    assert cues == untouched


def test_duplicate_voice_intervals_do_not_inflate_overlap():
    cues = [{"start": 0, "duration": 2, "text": "Test"}]
    turns = store.normalize_turns(
        [{"start": 0, "end": 1, "cluster": 5}, {"start": 0.5, "end": 1.5, "cluster": 5}],
        2,
    )
    assert store.align_captions(cues, turns, 2)[0]["overlap_seconds"] == {"speaker_1": 1.5}


@pytest.mark.parametrize(
    "turn",
    [
        {"start": -1, "end": 1, "cluster": 0},
        {"start": 0, "end": float("nan"), "cluster": 0},
        {"start": 0, "end": 1, "cluster": True},
        {"start": 4.5, "end": 4.9, "cluster": 0},
        {"start": 4, "end": 4.5, "cluster": 0},
    ],
)
def test_invalid_model_turns_rejected(turn):
    with pytest.raises(ValueError, match="invalid"):
        store.normalize_turns([turn], duration=4)


def test_empty_model_prediction_not_published():
    with pytest.raises(ValueError, match="No speech"):
        store.normalize_turns([], duration=4)


def test_local_audio_conversion_never_downloads_and_retains_input(tmp_path, monkeypatch):
    source = tmp_path / "original audio.wav"
    source.write_bytes(b"original")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(backend, "dependencies", lambda: (None, None, "/test/ffmpeg"))
    convert = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(backend.subprocess, "run", convert)
    result = backend.prepare_audio(work, "abcdefghijk", audio=source, sample_seconds=30)
    command = convert.call_args.args[0]
    assert command[command.index("-i") + 1] == str(source)
    assert command[command.index("-t") + 1] == "30"
    assert command[command.index("-ar") + 1] == "16000"
    assert command[command.index("-ac") + 1] == "1"
    assert "-vn" in command
    assert result == work / "audio.wav"
    assert source.read_bytes() == b"original"


def test_cli_diarize_rename_export_use_the_same_persistent_run(
    config, video, caption_bundle, fake_backend, capsys
):
    args = parser().parse_args(["diarize", video.video_id, "--sample-seconds", "4"])
    assert execute(args, config) == 0
    assert "Saved 2 voice clusters" in capsys.readouterr().out
    args = parser().parse_args(["speakers", "rename", video.video_id, "1", "Nikhil Kamath"])
    assert execute(args, config) == 0
    args = parser().parse_args(["speakers", "export", video.video_id])
    assert execute(args, config) == 0
    directory, base, state = store.load_run(caption_bundle)
    assert state["names"]["speaker_1"] == "Nikhil Kamath"
    assert "Nikhil Kamath" in (directory / "transcript.speakers.md").read_text()


def test_failed_edit_write_retains_name_and_audit_revision(
    caption_bundle, speaker_run, monkeypatch
):
    directory, base, state = speaker_run
    original = (directory / "edits.json").read_bytes()
    monkeypatch.setattr(store, "atomic_write", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        store.apply_edit(
            caption_bundle,
            run_id=base["run_id"],
            revision=0,
            names={"speaker_1": "Nikhil Kamath"},
        )
    assert (directory / "edits.json").read_bytes() == original
    assert store.load_run(caption_bundle)[2] == state


def test_bad_download_checksum_keeps_existing_models_and_cleans_scratch(tmp_path, monkeypatch):
    models = tmp_path / "models"
    models.mkdir()
    originals = {}
    for name in backend.MODEL_HASHES:
        path = models / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"previous model")
        originals[name] = path.read_bytes()
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.iter_content.return_value = [b"invalid downloaded archive"]
    monkeypatch.setattr(backend.requests, "get", Mock(return_value=response))
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        backend.setup_models(models)
    assert {name: (models / name).read_bytes() for name in originals} == originals
    assert not list(models.glob(".download-*"))


def test_missing_models_stop_before_audio_acquisition(config, video, caption_bundle, monkeypatch):
    prepare = Mock(side_effect=AssertionError("Must not acquire audio without verified models"))
    monkeypatch.setattr(backend, "prepare_audio", prepare)
    with pytest.raises(RuntimeError, match="speakers setup"):
        store.create_run(config, video.video_id, models=config.database.parent / "missing")
    prepare.assert_not_called()
    assert not (caption_bundle / "speakers" / "current.json").exists()
