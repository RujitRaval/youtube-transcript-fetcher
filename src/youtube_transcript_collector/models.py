from dataclasses import dataclass, field


@dataclass
class Video:
    video_id: str
    channel_name: str
    channel_url: str
    title: str
    video_url: str
    published_at: str | None = None
    duration_seconds: float | None = None


@dataclass
class Transcript:
    segments: list[dict]
    source: str
    language: str
    provider: str
    original: str | None = None
    original_format: str | None = None
    metadata: dict = field(default_factory=dict)
