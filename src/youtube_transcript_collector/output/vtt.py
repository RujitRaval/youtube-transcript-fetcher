import html
import re

from ..utils import timestamp


def render(segments):
    lines = ["WEBVTT", ""]
    for index, segment in enumerate(segments, 1):
        start = segment["start"]
        # Zero-duration events require at least one millisecond for a valid cue.
        end = max(start + segment["duration"], start + 0.001)
        text = html.escape(html.unescape(re.sub(r"<[^>]+>", "", segment["text"])), quote=False)
        text = "\n".join(line for line in text.splitlines() if line.strip())
        lines.extend(
            [str(index), f"{timestamp(start, vtt=True)} --> {timestamp(end, vtt=True)}", text, ""]
        )
    return "\n".join(lines) + "\n"
