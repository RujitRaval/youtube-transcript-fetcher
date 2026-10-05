"""Constrain a recording's remaining speakers without changing earlier model labels."""

import copy
import json
import math
import shutil
import tempfile
import uuid
import wave
from pathlib import Path

from ..database import writer_lock
from ..output.storage import atomic_write, json_text
from ..utils import iso, utcnow
from . import backend, store


def crop_audio(source, target, start_seconds):
    """Crop saved PCM audio at an exact frame; return its offset and full duration."""
    with wave.open(str(source), "rb") as audio:
        rate, frames = audio.getframerate(), audio.getnframes()
        if (rate, audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2):
            raise ValueError("Saved speaker audio must be 16 kHz mono PCM16")
        if not math.isfinite(start_seconds) or not 0 < start_seconds < frames / rate:
            raise ValueError("Refinement start must be inside the saved audio")
        start = round(start_seconds * rate)
        if not 0 < start < frames:
            raise ValueError("Refinement start must be inside the saved audio")
        audio.setpos(start)
        with wave.open(str(target), "wb") as section:
            section.setparams(audio.getparams())
            while chunk := audio.readframes(65536):
                section.writeframesraw(chunk)
    return start / rate, frames / rate


def refine_run(config, identifier, *, start_seconds, num_speakers, models, threads=2):
    if not math.isfinite(start_seconds) or start_seconds <= 0:
        raise ValueError("Refinement start must be positive and finite")
    if num_speakers is None:
        raise ValueError("Refinement requires a known speaker count")
    backend.validate_options(0.9, num_speakers, None, threads)
    with writer_lock(config.database):
        bundle = store.bundle_for(config, identifier)
        parent_dir, parent, parent_edits = store.load_run(bundle)
        source = parent_dir / "audio.wav"
        if backend.digest(source) != parent["audio_sha256"]:
            raise ValueError("Saved audio changed since analysis; run diarize again")
        backend.check_models(models)
        backend.dependencies()
        run_id = uuid.uuid4().hex
        with tempfile.TemporaryDirectory(dir=parent_dir.parent, prefix=".pending-") as scratch:
            scratch = Path(scratch)
            section = scratch / "section.wav"
            start, duration = crop_audio(source, section, start_seconds)
            predicted, info = backend.infer(
                section, models, num_speakers=num_speakers, threads=threads
            )
            section_duration = duration - start
            conversation = store.normalize_turns(predicted, section_duration)
            labels = list(dict.fromkeys(t["speaker_id"] for t in conversation))
            if len(labels) != num_speakers:
                raise ValueError(
                    "Model did not produce the requested voice count; previous run kept"
                )
            prefix = [
                dict(start=t["start"], end=min(t["end"], start), cluster=t["cluster"])
                for t in parent["turns"]
                if t["start"] < start
            ]
            offset = max((t["cluster"] for t in prefix), default=-1) + 1
            mapping = {label: offset + i for i, label in enumerate(labels)}
            shifted = [
                dict(
                    start=t["start"] + start, end=t["end"] + start, cluster=mapping[t["speaker_id"]]
                )
                for t in conversation
            ]
            identities = {
                t["cluster"]: t["speaker_id"] for t in parent["turns"] if t["start"] < start
            }
            next_id = (
                max((int(s.removeprefix("speaker_")) for s in identities.values()), default=0) + 1
            )
            identities.update(
                {mapping[label]: f"speaker_{next_id + i}" for i, label in enumerate(labels)}
            )
            turns = store.normalize_turns(prefix + shifted, duration)
            for turn in turns:
                turn["speaker_id"] = identities[turn["cluster"]]
            raw = json.loads((bundle / "transcript.raw.json").read_text(encoding="utf-8"))
            captions = store.align_captions(raw, turns, duration)
            # Keep wholly earlier model assignments and review flags byte-for-byte equivalent.
            earlier = {
                c["source_cue_index"]: c
                for c in parent["captions"]
                if c["start"] + c["duration"] <= start
            }
            captions = [copy.deepcopy(earlier.get(c["source_cue_index"], c)) for c in captions]
            created = iso(utcnow())
            constraint = dict(
                at=created,
                source="speakers refine command",
                start_seconds=start,
                requested_start_seconds=start_seconds,
                end_seconds=duration,
                num_speakers=num_speakers,
                method=(
                    "Preserve earlier model labels; analyze remaining audio with a fixed count; "
                    "use new voice IDs after the boundary."
                ),
            )
            info.update(
                section_audio_seconds=section_duration,
                audio_seconds=duration,
                scope=dict(start_seconds=start, end_seconds=duration, num_speakers=num_speakers),
                prefix_source_run_id=parent["run_id"],
            )
            base = copy.deepcopy(parent)
            base.update(
                run_id=run_id,
                parent_run_id=parent["run_id"],
                parent_revision=parent_edits["revision"],
                created_at=created,
                analysis=info,
                turns=turns,
                captions=captions,
                human_constraints=[*parent.get("human_constraints", []), constraint],
            )
            # Like diarize, refinement starts fresh edits. Parent names/history remain intact.
            state = dict(
                run_id=run_id,
                revision=0,
                names={
                    label: ""
                    for label in sorted(
                        set(identities.values()), key=lambda s: int(s.removeprefix("speaker_"))
                    )
                },
                overrides={},
                history=[],
            )
            shutil.copyfile(source, scratch / "audio.wav")
            if backend.digest(scratch / "audio.wav") != parent["audio_sha256"]:
                raise ValueError("Audio copy failed validation; previous run kept")
            section.unlink()
            atomic_write(scratch / "run.json", json_text(base))
            atomic_write(scratch / "edits.json", json_text(state))
            for name, content in store.exports(base, state).items():
                atomic_write(scratch / name, content)
            scratch.rename(parent_dir.parent / run_id)
        atomic_write(bundle / "speakers/current.json", json_text({"run_id": run_id}))
        return parent_dir.parent / run_id, base, state
