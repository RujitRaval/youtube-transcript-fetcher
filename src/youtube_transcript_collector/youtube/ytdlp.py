import logging
from datetime import UTC, datetime

from yt_dlp import YoutubeDL

from ..models import Video
from ..utils import video_id, watch_url

log = logging.getLogger(__name__)


class QuietLogger:
    def debug(self, message):
        log.debug("%s", message)

    def warning(self, message):
        log.debug("yt-dlp: %s", message)

    def error(self, message):
        log.debug("yt-dlp: %s", message)


def options(config):
    return dict(
        skip_download=True,
        quiet=True,
        no_warnings=True,
        logger=QuietLogger(),
        noplaylist=True,
        socket_timeout=config.network_timeout_seconds,
        retries=0,
        extractor_retries=0,
        fragment_retries=0,
        # Subtitle/metadata extraction should not require a downloadable media format.
        ignore_no_formats_error=True,
        cachedir=False,
        remote_components=set(),
    )


def publication(info):
    stamp = info.get("release_timestamp") or info.get("timestamp")
    if stamp:
        return datetime.fromtimestamp(stamp, UTC).isoformat(timespec="seconds")
    date = info.get("upload_date") or info.get("release_date")
    if date:
        try:
            return datetime.strptime(date, "%Y%m%d").date().isoformat()
        except ValueError:
            pass
    return None


def to_video(info, channel):
    identifier = video_id(info["id"])
    return Video(
        identifier,
        channel.name,
        channel.url,
        info.get("title") or identifier,
        watch_url(identifier),
        publication(info),
        info.get("duration"),
    )


def details(url, config):
    with YoutubeDL(options(config)) as ydl:
        return ydl.extract_info(url, download=False)
