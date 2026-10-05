from yt_dlp import YoutubeDL

from ..retry import RetrievalError
from .ytdlp import options, to_video


def discover(channel, config):
    url = channel.url.rstrip("/")
    if not url.endswith(("/videos", "/streams", "/shorts")):
        url += "/videos"
    opts = options(config) | dict(
        extract_flat=True,
        playlistend=config.recent_video_limit,
        lazy_playlist=True,
        noplaylist=False,
    )
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info:
            raise RetrievalError("network", f"No channel response for {channel.url}")
        videos = []
        # playlistend bounds upstream requests too, not just our returned list.
        for entry in info.get("entries") or []:
            if (
                entry
                and entry.get("id")
                and entry.get("live_status") not in {"is_live", "is_upcoming"}
            ):
                videos.append(to_video(entry, channel))
            if len(videos) >= config.recent_video_limit:
                break
        return videos
