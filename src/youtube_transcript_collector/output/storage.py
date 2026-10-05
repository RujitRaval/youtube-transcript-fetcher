import hashlib
import json
import os
import tempfile
from pathlib import Path

from ..utils import channel_folder, iso, slug, utcnow
from . import markdown, vtt


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".collector-", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def relative_path(video):
    date = video["published_at"][:10] if video["published_at"] else "unknown-date"
    year = date[:4] if video["published_at"] else "unknown-date"
    return (
        Path(channel_folder(video["channel_name"]))
        / year
        / (f"{date}-{slug(video['title'])}-{video['video_id']}")
    )


def target_path(root, relative):
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("Output path escapes configured storage directory")
    return target


def save(root, relative, video, transcript):
    target = target_path(root, relative)
    files = {
        "transcript.raw.json": json_text(transcript.segments),
        "transcript.md": markdown.render(video, transcript),
        "transcript.vtt": (
            transcript.original
            if transcript.original_format == "vtt"
            else vtt.render(transcript.segments)
        ),
    }
    if transcript.original is not None:
        files[f"transcript.original.{transcript.original_format}"] = transcript.original
    metadata = {
        key: video[key]
        for key in (
            "video_id",
            "title",
            "channel_name",
            "channel_url",
            "video_url",
            "published_at",
            "duration_seconds",
        )
    }
    metadata.update(
        channel=video["channel_name"],
        transcript_source=transcript.source,
        transcript_language=transcript.language,
        provider=transcript.provider,
        collected_at=iso(utcnow()),
        schema_version=1,
        operation_id=video["operation_id"],
        files={name: hashlib.sha256(text.encode()).hexdigest() for name, text in files.items()},
    )
    for name, text in files.items():
        atomic_write(target / name, text)
    # Commit marker: incomplete/mixed bundles will not validate on recovery.
    atomic_write(target / "metadata.json", json_text(metadata))
    return metadata


def recover(root, relative, identifier):
    if not relative:
        return None
    target = target_path(root, relative)
    try:
        metadata = json.loads((target / "metadata.json").read_text(encoding="utf-8"))
        if not isinstance(metadata, dict) or not isinstance(metadata.get("files"), dict):
            return None
        if any(
            not isinstance(metadata.get(key), str)
            for key in ("transcript_source", "transcript_language", "collected_at")
        ):
            return None
        required = {"transcript.raw.json", "transcript.md", "transcript.vtt"}
        if metadata["video_id"] != identifier or not required <= metadata["files"].keys():
            return None
        for name, digest in metadata["files"].items():
            if (
                Path(name).name != name
                or hashlib.sha256((target / name).read_bytes()).hexdigest() != digest
            ):
                return None
        return metadata
    except (OSError, ValueError, KeyError, TypeError):
        return None
