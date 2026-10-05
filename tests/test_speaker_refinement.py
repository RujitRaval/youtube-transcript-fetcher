import json
import shutil
import struct
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from youtube_transcript_collector.cli import execute, parser
from youtube_transcript_collector.models import Transcript
from youtube_transcript_collector.output import storage
from youtube_transcript_collector.speakers import backend, refine, store


def write_wav(path, *, rate=16000, channels=1, width=2):
    samples = b"".join(struct.pack("<h", index % 30000) for index in range(64000))
    with wave.open(str(path), "wb") as audio:
        audio.setparams((channels, width, rate, 0, "NONE", "not compressed"))
        audio.writeframes(samples)
    return samples


@pytest.fixture
def refinement(config, db, video, tmp_path, monkeypatch):
    db.insert(video)
    row = db.get(video.video_id)
    relative = storage.relative_path(row)
    captions = [
        {"start": 0.0, "duration": 0.5, "text": "Earlier words"},
        {"start": 1.0, "duration": 0.5, "text": "- Yes\n- Hello"},
        {"start": 1.75, "duration": 0.5, "text": "Across the boundary"},
        {"start": 2.5, "duration": 0.5, "text": "After the boundary"},
        {"start": 3.0, "duration": 1.0, "text": "Final words"},
    ]
    storage.save(
        config.output_directory, relative, row, Transcript(captions, "youtube-manual", "en", "test")
    )
    db.update(video.video_id, status="complete", output_path=str(relative))
    bundle = config.output_directory / relative
    source = tmp_path / "original.wav"
    samples = write_wav(source)
    monkeypatch.setattr(backend, "check_models", Mock())
    monkeypatch.setattr(backend, "dependencies", Mock())

    def prepare(directory, identifier, **kwargs):
        target = directory / "audio.wav"
        shutil.copyfile(source, target)
        return target

    monkeypatch.setattr(backend, "prepare_audio", prepare)
    infer = Mock(
        return_value=(
            [
                {"start": 0, "end": 1, "cluster": 7},
                {"start": 1, "end": 2.5, "cluster": 2},
                {"start": 2.5, "end": 4, "cluster": 7},
            ],
            {"audio_seconds": 4.0, "inference_seconds": 0.1},
        )
    )
    monkeypatch.setattr(backend, "infer", infer)
    directory, base, _ = store.create_run(
        config, video.video_id, models=Path("unused"), audio=source
    )
    state = store.apply_edit(
        bundle,
        run_id=base["run_id"],
        revision=0,
        names={"speaker_1": "Existing name"},
        overrides={"1": ["speaker_1"]},
    )
    store.export_run(bundle)
    snapshot = {p.name: p.read_bytes() for p in directory.iterdir()}
    core = {p.name: p.read_bytes() for p in bundle.iterdir() if p.is_file()}
    prepare = Mock(side_effect=AssertionError("Refinement must reuse saved audio"))
    monkeypatch.setattr(backend, "prepare_audio", prepare)
    infer.reset_mock()

    def infer_section(path, models, **kwargs):
        with wave.open(str(path), "rb") as section:
            assert section.getnframes() == 32000
            assert section.readframes(32000) == samples[64000:]
        return (
            [
                {"start": 0, "end": 1, "cluster": 7},
                {"start": 1, "end": 2, "cluster": 2},
            ],
            {"audio_seconds": 2.0, "inference_seconds": 0.05},
        )

    infer.side_effect = infer_section
    return SimpleNamespace(
        config=config,
        identifier=video.video_id,
        bundle=bundle,
        directory=directory,
        base=base,
        state=state,
        snapshot=snapshot,
        core=core,
        infer=infer,
        prepare=prepare,
    )


def run_refinement(case, **kwargs):
    return refine.refine_run(
        case.config,
        case.identifier,
        models=Path("unused"),
        **({"start_seconds": 2, "num_speakers": 2} | kwargs),
    )


def assert_parent_unchanged(case, *, current=True):
    assert {p.name: p.read_bytes() for p in case.directory.iterdir()} == case.snapshot
    assert {p.name: p.read_bytes() for p in case.bundle.iterdir() if p.is_file()} == case.core
    assert store.load_run(case.bundle, case.base["run_id"]) == (
        case.directory,
        case.base,
        case.state,
    )
    if current:
        assert store.load_run(case.bundle)[0] == case.directory
    assert not list(case.directory.parent.glob(".pending-*"))
    case.prepare.assert_not_called()


def test_crop_uses_nearest_pcm_frame_and_keeps_source(tmp_path):
    source, target = tmp_path / "source.wav", tmp_path / "section.wav"
    samples = write_wav(source)
    original = source.read_bytes()
    offset, duration = refine.crop_audio(source, target, 2.00004)
    assert offset == 32001 / 16000
    assert duration == 4
    with wave.open(str(target), "rb") as section:
        assert (section.getframerate(), section.getnchannels(), section.getsampwidth()) == (
            16000,
            1,
            2,
        )
        assert section.getnframes() == 31999
        assert section.readframes(64000) == samples[64002:]
    assert source.read_bytes() == original


@pytest.mark.parametrize("options", [{"rate": 8000}, {"channels": 2}, {"width": 1}])
def test_crop_rejects_unsupported_saved_audio(tmp_path, options):
    source = tmp_path / "source.wav"
    write_wav(source, **options)
    with pytest.raises(ValueError, match="PCM16"):
        refine.crop_audio(source, tmp_path / "section.wav", 1)


def test_refinement_preserves_prefix_and_publishes_distinct_section_voices(refinement):
    case = refinement
    directory, base, state = run_refinement(case, start_seconds=2.00002)
    assert directory != case.directory
    assert store.load_run(case.bundle) == (directory, base, state)
    assert base["turns"][:2] == [case.base["turns"][0], case.base["turns"][1] | {"end": 2.0}]
    assert base["captions"][:2] == case.base["captions"][:2]
    assert base["captions"][1]["review_reasons"] == ["multiple_caption_lines"]
    assert [(t["start"], t["end"], t["speaker_id"]) for t in base["turns"][2:]] == [
        (2.0, 3.0, "speaker_3"),
        (3.0, 4.0, "speaker_4"),
    ]
    assert base["captions"][2]["review_reasons"] == ["multiple_voices"]
    assert base["captions"][3]["speaker_ids"] == ["speaker_3"]
    assert base["parent_run_id"] == case.base["run_id"]
    assert base["parent_revision"] == 1
    constraint = base["human_constraints"][-1]
    assert constraint["requested_start_seconds"] == 2.00002
    assert constraint["start_seconds"] == 2
    assert constraint["end_seconds"] == 4
    assert constraint["num_speakers"] == 2
    assert constraint["source"] == "speakers refine command"
    assert base["analysis"]["prefix_source_run_id"] == case.base["run_id"]
    assert base["analysis"]["section_audio_seconds"] == 2
    assert base["analysis"]["audio_seconds"] == 4
    assert base["analysis"]["scope"] == dict(start_seconds=2, end_seconds=4, num_speakers=2)
    assert state == dict(
        run_id=base["run_id"],
        revision=0,
        names={f"speaker_{i}": "" for i in range(1, 5)},
        overrides={},
        history=[],
    )
    assert (directory / "audio.wav").read_bytes() == case.snapshot["audio.wav"]
    assert not (directory / "section.wav").exists()
    assert json.loads((directory / "transcript.speakers.json").read_text()) == {
        "analysis": base,
        "edits": state,
    }
    assert "Speaker 3" in (directory / "transcript.speakers.md").read_text()
    assert "<v Speaker 4>Final words</v>" in (directory / "transcript.speakers.vtt").read_text()
    case.infer.assert_called_once()
    assert case.infer.call_args.kwargs == {"num_speakers": 2, "threads": 2}
    assert_parent_unchanged(case, current=False)
    with pytest.raises(ValueError, match="reload"):
        store.apply_edit(case.bundle, run_id=case.base["run_id"], revision=1)


def test_repeated_refinement_retains_constraint_history(refinement):
    _, first, _ = run_refinement(refinement)
    _, second, _ = run_refinement(refinement)
    assert second["parent_run_id"] == first["run_id"]
    assert second["parent_revision"] == 0
    assert second["human_constraints"][:-1] == first["human_constraints"]
    assert len(second["human_constraints"]) == 2


def test_clipping_tied_start_turns_never_reassigns_existing_voice_ids(refinement):
    case = refinement
    case.base["turns"] = store.normalize_turns(
        [
            {"start": 0, "end": 1, "cluster": 7},
            {"start": 0, "end": 2, "cluster": 2},
        ],
        4,
    )
    storage.atomic_write(case.directory / "run.json", storage.json_text(case.base))
    case.snapshot["run.json"] = (case.directory / "run.json").read_bytes()

    def infer_section(path, models, **kwargs):
        with wave.open(str(path), "rb") as section:
            assert section.getnframes() == 56000
        return (
            [
                {"start": 0, "end": 1.5, "cluster": 7},
                {"start": 1.5, "end": 3.5, "cluster": 2},
            ],
            {"audio_seconds": 3.5, "inference_seconds": 0.05},
        )

    case.infer.side_effect = infer_section
    _, base, state = run_refinement(case, start_seconds=0.5)
    prefix = [turn for turn in base["turns"] if turn["start"] < 0.5]
    # Clipping makes both intervals identical; sorting by cluster must not rename them.
    assert {turn["cluster"]: turn["speaker_id"] for turn in prefix} == {
        7: "speaker_1",
        2: "speaker_2",
    }
    assert all(turn["end"] == 0.5 for turn in prefix)
    assert base["captions"][0] == case.base["captions"][0]
    assert base["captions"][0]["speaker_ids"] == ["speaker_1"]
    assert {turn["speaker_id"] for turn in base["turns"] if turn["start"] >= 0.5} == {
        "speaker_3",
        "speaker_4",
    }
    assert list(state["names"]) == ["speaker_1", "speaker_2", "speaker_3", "speaker_4"]
    assert_parent_unchanged(case, current=False)


@pytest.mark.parametrize("start", [-1, 0, float("nan"), float("inf"), -float("inf"), 4, 5, 1e308])
def test_invalid_boundary_keeps_parent_current(refinement, start):
    with pytest.raises(ValueError, match="Refinement start"):
        run_refinement(refinement, start_seconds=start)
    refinement.infer.assert_not_called()
    assert_parent_unchanged(refinement)


@pytest.mark.parametrize("count", [None, 0, -1, 51])
def test_invalid_voice_count_keeps_parent_current(refinement, count):
    with pytest.raises(ValueError):
        run_refinement(refinement, num_speakers=count)
    refinement.infer.assert_not_called()
    assert_parent_unchanged(refinement)


def test_wrong_model_voice_count_keeps_parent_current(refinement):
    refinement.infer.side_effect = None
    refinement.infer.return_value = ([{"start": 0, "end": 2, "cluster": 7}], {"audio_seconds": 2})
    with pytest.raises(ValueError, match="requested voice count"):
        run_refinement(refinement)
    assert_parent_unchanged(refinement)


def test_changed_saved_audio_rejected_before_inference(refinement):
    audio = refinement.directory / "audio.wav"
    audio.write_bytes(b"corrupted saved audio")
    refinement.snapshot["audio.wav"] = audio.read_bytes()
    with pytest.raises(ValueError, match="Saved audio changed"):
        run_refinement(refinement)
    refinement.infer.assert_not_called()
    assert_parent_unchanged(refinement)


@pytest.mark.parametrize(
    "stage", ["inference", "run.json", "edits.json", "transcript.speakers.vtt", "current.json"]
)
def test_failed_refinement_keeps_parent_current_and_cleans_scratch(refinement, monkeypatch, stage):
    original_write = refine.atomic_write

    def fail_write(path, content):
        if path.name == stage:
            raise OSError("disk full")
        return original_write(path, content)

    monkeypatch.setattr(refine, "atomic_write", fail_write)
    if stage == "inference":
        refinement.infer.side_effect = RuntimeError("model failed")
    with pytest.raises((OSError, RuntimeError)):
        run_refinement(refinement)
    assert_parent_unchanged(refinement)
    for directory in refinement.directory.parent.iterdir():
        assert (directory / "run.json").is_file()
        assert (directory / "edits.json").is_file()
        assert (directory / "audio.wav").is_file()


def test_cli_refine_runs_pipeline_and_saves_exports(refinement, capsys):
    args = parser().parse_args(
        [
            "speakers",
            "refine",
            refinement.identifier,
            "--from-seconds",
            "2",
            "--num-speakers",
            "2",
            "--threads",
            "3",
        ]
    )
    assert execute(args, refinement.config) == 0
    output = capsys.readouterr().out
    assert "Saved 4 voice clusters" in output
    assert refinement.base["run_id"] in output
    directory, base, _ = store.load_run(refinement.bundle)
    assert base["parent_run_id"] == refinement.base["run_id"]
    assert (directory / "transcript.speakers.json").is_file()
    assert refinement.infer.call_args.kwargs == {"num_speakers": 2, "threads": 3}
    assert_parent_unchanged(refinement, current=False)


@pytest.mark.parametrize("arguments", [[], ["--from-seconds", "2"], ["--num-speakers", "2"]])
def test_cli_refine_requires_boundary_and_count(arguments):
    with pytest.raises(SystemExit) as exc:
        parser().parse_args(["speakers", "refine", "abcdefghijk", *arguments])
    assert exc.value.code == 2
