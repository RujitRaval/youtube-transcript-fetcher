import json
import logging
import math
import re
import time
from html import unescape
from urllib.parse import parse_qs, urlparse

import requests
from youtube_transcript_api import YouTubeTranscriptApi
from yt_dlp import YoutubeDL

from ..models import Transcript
from ..retry import RetrievalError, classify
from ..utils import watch_url
from .ytdlp import options

log = logging.getLogger(__name__)


class TimeoutSession(requests.Session):
    def __init__(self, timeout):
        super().__init__()
        self.timeout = timeout

    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        return super().request(*args, **kwargs)


def language_rank(code, generated, languages):
    """Manual English, automatic English, then requested language order and manual first."""
    code = code.lower()
    if code == "en" or code.startswith("en-"):
        return (0, int(generated), 0 if code == "en" else 1, code)
    for index, preferred in enumerate(languages):
        preferred = preferred.lower()
        if code == preferred or code.startswith(preferred + "-"):
            return (1, index, int(generated), code)
    return None


def validate_segments(segments):
    if not segments:
        raise RetrievalError("transcript_unavailable", "Caption track contains no timed text")
    for segment in segments:
        if not isinstance(segment.get("text"), str):
            raise RetrievalError("unexpected", "Caption segment has no text")
        for field in ("start", "duration"):
            value = segment.get(field)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise RetrievalError("unexpected", f"Invalid caption {field}: {value!r}")
    return segments


def fetch_api(identifier, config):
    with TimeoutSession(config.network_timeout_seconds) as session:
        api = YouTubeTranscriptApi(http_client=session)
        candidates = []
        for track in api.list(identifier):
            rank = language_rank(
                track.language_code, track.is_generated, config.preferred_languages
            )
            if rank is not None:
                candidates.append((rank, track))
        if not candidates:
            raise RetrievalError(
                "transcript_unavailable", "No caption track in a preferred language"
            )
        track = min(candidates, key=lambda pair: pair[0])[1]
        segments = track.fetch(preserve_formatting=True).to_raw_data()
        return Transcript(
            validate_segments(segments),
            "youtube-auto" if track.is_generated else "youtube-manual",
            track.language_code,
            "youtube-transcript-api",
        )


def parse_json3(payload):
    segments = []
    for event in json.loads(payload).get("events", []):
        if "segs" not in event or "tStartMs" not in event:
            continue
        text = "".join(seg.get("utf8", "") for seg in event["segs"])
        if not text.strip():
            continue
        segments.append(
            dict(
                start=event["tStartMs"] / 1000,
                duration=event.get("dDurationMs", 0) / 1000,
                text=text,
            )
        )
    return validate_segments(segments)


def parse_vtt(payload):
    def seconds(value):
        parts = value.split(":")
        return sum(float(part) * 60**index for index, part in enumerate(reversed(parts)))

    segments = []
    for block in re.split(r"\n\s*\n", payload.replace("\r\n", "\n").lstrip("\ufeff")):
        lines = block.splitlines()
        if not lines or lines[0].startswith(("NOTE", "STYLE", "REGION", "WEBVTT")):
            continue
        for index, line in enumerate(lines):
            match = re.match(
                r"((?:\d+:)?\d{2}:\d{2}\.\d{3})\s+-->\s+((?:\d+:)?\d{2}:\d{2}\.\d{3})", line
            )
            if match:
                start, end = map(seconds, match.groups())
                # Keep original VTT separately; canonical text omits positioning/inline time tags.
                text = unescape(re.sub(r"<[^>]+>", "", "\n".join(lines[index + 1 :])))
                if text.strip():
                    segments.append(dict(start=start, duration=end - start, text=text))
                break
    return validate_segments(segments)


def fetch_ytdlp(identifier, config):
    with YoutubeDL(options(config)) as ydl:
        info = ydl.extract_info(watch_url(identifier), download=False)
        if not info:
            raise RetrievalError("network", "yt-dlp returned no video information")
        if info.get("live_status") in {"is_live", "is_upcoming"}:
            raise RetrievalError(
                "transcript_unavailable", "Live/upcoming video: wait for final captions"
            )
        candidates = []
        for generated, field in ((False, "subtitles"), (True, "automatic_captions")):
            for language, formats in (info.get(field) or {}).items():
                rank = language_rank(language, generated, config.preferred_languages)
                if rank is None:
                    continue
                for fmt in formats:
                    # yt-dlp exposes YouTube machine translations too. Select native tracks only.
                    if parse_qs(urlparse(fmt.get("url", "")).query).get("tlang"):
                        continue
                    if fmt.get("ext") in {"json3", "vtt"} and fmt.get("url"):
                        candidates.append(
                            (rank, 0 if fmt["ext"] == "json3" else 1, generated, language, fmt)
                        )
        if not candidates:
            raise RetrievalError(
                "transcript_unavailable", "yt-dlp found no supported preferred captions"
            )
        _, _, generated, language, fmt = min(candidates, key=lambda item: item[:2])
        # Fetch only the selected subtitle resource, never a media stream.
        with ydl.urlopen(fmt["url"]) as response:
            payload = response.read(20_000_001)
        if len(payload) > 20_000_000:
            raise RetrievalError("unexpected", "Caption response exceeds 20 MB")
        payload = payload.decode("utf-8-sig")
        segments = parse_json3(payload) if fmt["ext"] == "json3" else parse_vtt(payload)
        return Transcript(
            segments,
            "youtube-auto" if generated else "youtube-manual",
            language,
            "yt-dlp",
            payload,
            fmt["ext"],
            info,
        )


def retrieve(identifier, config):
    try:
        return fetch_api(identifier, config)
    except Exception as exc:
        first = classify(exc)
        if first.kind in {"rate_limited", "unavailable"}:
            raise first from exc
        log.info("Primary captions unavailable (%s); trying yt-dlp", first.kind)
        log.debug("Primary error: %s", first)
    time.sleep(config.request_delay_seconds)
    try:
        return fetch_ytdlp(identifier, config)
    except Exception as exc:
        second = classify(exc)
        # A fallback with no captions must not hide an earlier network/application failure.
        if second.kind == "transcript_unavailable" and first.kind != "transcript_unavailable":
            raise first from exc
        raise second from exc
