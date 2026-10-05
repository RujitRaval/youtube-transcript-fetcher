import hashlib
import re
import unicodedata
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def slug(text: str, limit: int = 70) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit].rstrip("-") or "untitled"


def channel_folder(name: str) -> str:
    return f"{slug(name)}-{hashlib.sha256(name.encode()).hexdigest()[:8]}"


def timestamp(seconds: float, *, vtt: bool = False) -> str:
    millis = max(0, round(seconds * 1000))
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    if vtt:
        return f"{hours:02}:{minutes:02}:{secs:02}.{ms:03}"
    return f"{hours:02}:{minutes:02}:{secs:02}" if hours else f"{minutes:02}:{secs:02}"


def video_id(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    url = urlparse(value)
    candidate = ""
    if url.scheme == "https" and not url.username and not url.port:
        if url.hostname in {"youtu.be", "www.youtu.be"}:
            candidate = url.path.strip("/")
        elif url.hostname in YOUTUBE_HOSTS:
            if url.path == "/watch":
                candidate = parse_qs(url.query).get("v", [""])[0]
            elif re.match(r"^/(shorts|live|embed)/", url.path):
                candidate = url.path.split("/")[2]
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
        raise ValueError("Provide an 11-character video ID or an HTTPS YouTube video URL")
    return candidate


def watch_url(identifier: str) -> str:
    return f"https://www.youtube.com/watch?v={identifier}"
