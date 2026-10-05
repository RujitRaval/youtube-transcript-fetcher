import re
from html import unescape

from ..utils import timestamp


def plain(text):
    text = unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"([\\`*_{}\[\]<>#!|])", r"\\\1", text)


def render(video, transcript):
    duration = (
        timestamp(video["duration_seconds"]) if video["duration_seconds"] is not None else "Unknown"
    )
    lines = [
        f"# {plain(video['title']).replace(chr(10), ' ')}",
        "",
        f"**Channel:** {plain(video['channel_name'])}",
        f"**Published:** {video['published_at'] or 'Unknown'}",
        f"**Duration:** {duration}",
        f"**Source:** {transcript.source} ({transcript.language})",
        f"**Video:** <{video['video_url']}>",
        "",
        "---",
        "",
        "## Transcript",
        "",
    ]
    for segment in transcript.segments:
        lines.extend([f"**[{timestamp(segment['start'])}]**", "", plain(segment["text"]), ""])
    return "\n".join(lines)
