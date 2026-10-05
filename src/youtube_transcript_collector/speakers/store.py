"""Immutable model runs plus one atomic, revisioned human-edit document per run."""

import copy
import html
import json
import math
import re
import tempfile
import uuid
from pathlib import Path

from ..database import Database, writer_lock
from ..output.storage import atomic_write, json_text, recover, target_path
from ..utils import iso, timestamp, utcnow
from . import backend


def bundle_for(config, identifier):
    with Database(config.database) as db:
        row = db.get(identifier)
    if not row or row["status"] != "complete" or not row["output_path"]:
        raise ValueError("Collect this video's captions first with 'collect VIDEO_URL'")
    if not recover(config.output_directory, row["output_path"], identifier):
        raise ValueError("Caption bundle failed validation; collect it again with --force")
    return target_path(config.output_directory, row["output_path"])


def normalize_turns(raw, duration):
    labels, turns = {}, []
    for turn in sorted(raw, key=lambda t: (t["start"], t["end"], t["cluster"])):
        start, end, cluster = turn["start"], turn["end"], turn["cluster"]
        if (
            not all(math.isfinite(v) for v in (start, end))
            or start < 0
            or start >= duration
            or end <= start
            or end > duration + 1
            or type(cluster) is not int
            or cluster < 0
        ):
            raise ValueError("Diarizer returned invalid speaker intervals")
        label = labels.setdefault(cluster, f"speaker_{len(labels) + 1}")
        turns.append(dict(start=start, end=min(end, duration), speaker_id=label, cluster=cluster))
    if not turns:
        raise ValueError("No speech detected; no speaker run was saved")
    return turns


def union_seconds(intervals):
    total, end = 0.0, -1.0
    for a, b in sorted(intervals):
        total += max(0, b - max(a, end))
        end = max(end, b)
    return total


def align_captions(captions, turns, duration):
    """Time overlap only; never split words or invent identities/confidence scores."""
    output, active, cursor = [], [], 0
    for index, cue in sorted(enumerate(captions), key=lambda item: item[1]["start"]):
        start, end = cue["start"], cue["start"] + cue["duration"]
        if start >= duration:
            continue
        while cursor < len(turns) and turns[cursor]["start"] < end:
            active.append(turns[cursor])
            cursor += 1
        active = [t for t in active if t["end"] > start]
        intervals = {}
        for turn in active:
            a, b = max(start, turn["start"]), min(end, turn["end"])
            if b > a:
                intervals.setdefault(turn["speaker_id"], []).append((a, b))
        overlap = {label: union_seconds(spans) for label, spans in intervals.items()}
        candidates = sorted(overlap, key=lambda label: (-overlap[label], label))
        substantial = [s for s in candidates if overlap[s] >= max(0.15, cue["duration"] * 0.15)]
        reasons = []
        if len(substantial) > 1:
            reasons.append("multiple_voices")
        if not substantial or overlap[substantial[0]] < cue["duration"] * 0.5:
            reasons.append("insufficient_audio_match")
        if sum(line.lstrip().startswith("-") for line in cue["text"].splitlines()) > 1:
            reasons.append("multiple_caption_lines")
        if end > duration:
            reasons.append("audio_boundary")
        output.append(
            dict(
                source_cue_index=index,
                **cue,
                speaker_ids=[substantial[0]] if substantial and not reasons else [],
                candidate_speakers=candidates,
                overlap_seconds=overlap,
                review_reasons=reasons,
            )
        )
    return output


def create_run(
    config,
    identifier,
    *,
    models,
    audio=None,
    sample_seconds=None,
    threshold=0.9,
    num_speakers=None,
    threads=2,
):
    backend.validate_options(threshold, num_speakers, sample_seconds, threads)
    backend.check_models(models)
    backend.dependencies()
    with writer_lock(config.database):
        bundle = bundle_for(config, identifier)
        raw = bundle / "transcript.raw.json"
        captions = json.loads(raw.read_text(encoding="utf-8"))
        runs = bundle / "speakers" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        run_id = uuid.uuid4().hex
        with tempfile.TemporaryDirectory(dir=runs, prefix=".pending-") as scratch:
            scratch = Path(scratch)
            wav = backend.prepare_audio(
                scratch,
                identifier,
                audio=audio,
                sample_seconds=sample_seconds,
                timeout=config.network_timeout_seconds,
            )
            predicted, info = backend.infer(
                wav, models, threshold=threshold, num_speakers=num_speakers, threads=threads
            )
            duration = info["audio_seconds"]
            turns = normalize_turns(predicted, duration)
            base = dict(
                schema_version=1,
                run_id=run_id,
                video_id=identifier,
                title=json.loads((bundle / "metadata.json").read_text())["title"],
                created_at=iso(utcnow()),
                source_sha256=backend.digest(raw),
                audio_sha256=backend.digest(wav),
                audio_source="local_file" if audio else "youtube",
                partial=duration + 1 < max(c["start"] + c["duration"] for c in captions),
                analysis=info,
                turns=turns,
                captions=align_captions(captions, turns, duration),
            )
            labels = list(dict.fromkeys(t["speaker_id"] for t in turns))
            state = dict(
                run_id=run_id, revision=0, names={s: "" for s in labels}, overrides={}, history=[]
            )
            atomic_write(scratch / "run.json", json_text(base))
            atomic_write(scratch / "edits.json", json_text(state))
            scratch.rename(runs / run_id)
        # Publishing this pointer is the commit; failed/interrupted runs retain the previous run.
        atomic_write(bundle / "speakers" / "current.json", json_text({"run_id": run_id}))
        return runs / run_id, base, state


def load_run(bundle, run_id=None):
    if run_id is None:
        pointer = bundle / "speakers" / "current.json"
        if not pointer.exists():
            raise ValueError("No speaker analysis yet; run 'diarize VIDEO_URL'")
        run_id = json.loads(pointer.read_text())["run_id"]
    if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("Invalid speaker run ID")
    directory = bundle / "speakers" / "runs" / run_id
    base = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    state = json.loads((directory / "edits.json").read_text(encoding="utf-8"))
    if (
        base.get("schema_version") != 1
        or base.get("run_id") != run_id
        or state.get("run_id") != run_id
    ):
        raise ValueError("Invalid speaker run data")
    if backend.digest(bundle / "transcript.raw.json") != base["source_sha256"]:
        raise ValueError("Captions changed since speaker analysis; run diarize again")
    return directory, base, state


def apply_edit(bundle, *, run_id, revision, names=None, overrides=None):
    """Caller holds the collector writer lock. One atomic file includes state and audit history."""
    directory, base, current = load_run(bundle)
    if run_id != base["run_id"] or type(revision) is not int or revision != current["revision"]:
        raise ValueError("Speaker review changed; reload before saving")
    updated = copy.deepcopy(current)
    changes = {}
    if names is not None:
        if not isinstance(names, dict) or names.keys() - current["names"].keys():
            raise ValueError("Unknown speaker ID in name mapping")
        for label, name in names.items():
            if not isinstance(name, str) or len(name) > 120 or any(ord(c) < 32 for c in name):
                raise ValueError("Speaker names must be single-line text of at most 120 characters")
            updated["names"][label] = name.strip()
        changes["names"] = {
            s: {"before": current["names"][s], "after": v}
            for s, v in updated["names"].items()
            if v != current["names"][s]
        }
    if overrides is not None:
        valid = {str(c["source_cue_index"]) for c in base["captions"]}
        if not isinstance(overrides, dict) or overrides.keys() - valid:
            raise ValueError("Unknown caption index")
        for index, ids in overrides.items():
            if ids is None:
                updated["overrides"].pop(index, None)
            elif (
                not isinstance(ids, list)
                or not ids
                or any(not isinstance(s, str) or s not in current["names"] for s in ids)
                or len(set(ids)) != len(ids)
            ):
                raise ValueError("Select one or more valid speakers for a caption")
            else:
                updated["overrides"][index] = ids
        changes["overrides"] = {
            i: {"before": current["overrides"].get(i), "after": updated["overrides"].get(i)}
            for i in overrides
            if current["overrides"].get(i) != updated["overrides"].get(i)
        }
    changes = {k: v for k, v in changes.items() if v}
    if changes:
        updated["revision"] += 1
        updated["history"].append(
            dict(revision=updated["revision"], at=iso(utcnow()), changes=changes)
        )
        atomic_write(directory / "edits.json", json_text(updated))
    return updated


def display_name(label, state):
    return state["names"].get(label) or "Speaker " + label.removeprefix("speaker_")


def rendered_cues(base, state):
    for cue in base["captions"]:
        manual = str(cue["source_cue_index"]) in state["overrides"]
        ids = state["overrides"].get(str(cue["source_cue_index"]), cue["speaker_ids"])
        needs_review = not manual and bool(cue["review_reasons"])
        shown = ids or cue["candidate_speakers"]
        label = " / ".join(display_name(s, state) for s in shown) or "Review required"
        if needs_review and shown:
            label += " (review required)"
        yield cue, label


def exports(base, state):
    markdown = [
        f"# {html.escape(base['title'])}",
        "",
        "Speaker labels are model predictions unless manually corrected.",
        f"Run: {base['run_id']} · Revision: {state['revision']} · Partial audio: {base['partial']}",
        "",
    ]
    vtt = [
        "WEBVTT",
        "",
        f"NOTE Speaker analysis run {base['run_id']}; partial audio: {base['partial']}",
        "",
    ]
    for cue, label in rendered_cues(base, state):
        # Escape Markdown metacharacters in speaker names; VTT escapes angle brackets separately.
        safe = re.sub(r"([\\`*_{}\[\]<>])", r"\\\1", label)
        markdown += [f"**[{timestamp(cue['start'])}] {safe}**", "", cue["text"], ""]
        vtt += [
            f"{timestamp(cue['start'], vtt=True)} --> "
            f"{timestamp(cue['start'] + cue['duration'], vtt=True)}",
            f"<v {html.escape(label)}>{html.escape(cue['text'])}</v>",
            "",
        ]
    return {
        "transcript.speakers.md": "\n".join(markdown),
        "transcript.speakers.vtt": "\n".join(vtt),
        "transcript.speakers.json": json_text({"analysis": base, "edits": state}),
    }


def export_run(bundle):
    directory, base, state = load_run(bundle)
    for name, text in exports(base, state).items():
        atomic_write(directory / name, text)
    return directory
