import math
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .utils import YOUTUBE_HOSTS


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Channel:
    name: str
    url: str


@dataclass(frozen=True)
class Config:
    output_directory: Path
    database: Path
    channels: tuple[Channel, ...]
    recent_video_limit: int = 10
    max_videos_per_run: int = 20
    request_delay_seconds: float = 2
    network_timeout_seconds: float = 20
    preferred_languages: tuple[str, ...] = ("en", "en-orig")
    max_attempts: int = 6
    backoff_hours: tuple[float, ...] = (1, 3, 12, 24, 48)
    rate_limit_cooldown_hours: float = 6


def mapping(value, label, keys):
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be a mapping")
    unknown = value.keys() - set(keys)
    if unknown:
        raise ConfigError(f"Unknown {label} option(s): {', '.join(map(str, unknown))}")
    return value


def number(value, label, *, integer=False, minimum=0.001, maximum=1_000_000):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{label} must be a number")
    if integer and not isinstance(value, int):
        raise ConfigError(f"{label} must be an integer")
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ConfigError(f"{label} must be between {minimum} and {maximum}")
    return value


def load_config(path: Path) -> Config:
    path = path.expanduser().resolve()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Cannot read config {path}: {exc}") from exc
    raw = mapping(
        raw, "config", ["storage", "collector", "transcripts", "channels", "local_transcription"]
    )
    storage = mapping(raw.get("storage", {}), "storage", ["output_directory", "database"])

    def resolve(key, default):
        value = storage.get(key, default)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"storage.{key} must be a nonempty path string")
        return (path.parent / Path(value).expanduser()).resolve()

    output, db = (
        resolve("output_directory", "./transcripts"),
        resolve("database", "./data/collector.db"),
    )
    if output == db or db in output.parents:
        raise ConfigError("storage database path conflicts with output_directory")
    collector = mapping(
        raw.get("collector", {}),
        "collector",
        [
            "recent_video_limit",
            "max_videos_per_run",
            "request_delay_seconds",
            "network_timeout_seconds",
        ],
    )
    kwargs = {}
    for key, default, integer, minimum, maximum in [
        ("recent_video_limit", 10, True, 1, 1000),
        ("max_videos_per_run", 20, True, 1, 1000),
        ("request_delay_seconds", 2, False, 0, 300),
        ("network_timeout_seconds", 20, False, 1, 300),
    ]:
        kwargs[key] = number(
            collector.get(key, default),
            f"collector.{key}",
            integer=integer,
            minimum=minimum,
            maximum=maximum,
        )
    transcripts = mapping(
        raw.get("transcripts", {}), "transcripts", ["preferred_languages", "retry"]
    )
    languages = transcripts.get("preferred_languages", ["en", "en-orig"])
    if (
        not isinstance(languages, list)
        or not languages
        or any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", v) for v in languages)
    ):
        raise ConfigError(
            "transcripts.preferred_languages must be a nonempty list of language codes"
        )
    retry = mapping(
        transcripts.get("retry", {}),
        "transcripts.retry",
        ["max_attempts", "backoff_hours", "rate_limit_cooldown_hours"],
    )
    attempts = number(
        retry.get("max_attempts", 6), "retry.max_attempts", integer=True, minimum=1, maximum=100
    )
    hours = retry.get("backoff_hours", [1, 3, 12, 24, 48])
    if not isinstance(hours, list) or not hours:
        raise ConfigError("retry.backoff_hours must be a nonempty list of positive hours")
    hours = tuple(number(v, "retry.backoff_hours", maximum=8760) for v in hours)
    cooldown = number(
        retry.get("rate_limit_cooldown_hours", 6),
        "retry.rate_limit_cooldown_hours",
        minimum=1,
        maximum=8760,
    )
    local = mapping(raw.get("local_transcription", {}), "local_transcription", ["enabled"])
    if type(local.get("enabled", False)) is not bool:
        raise ConfigError("local_transcription.enabled must be true or false")
    if local.get("enabled", False):
        raise ConfigError(
            "Local transcription has no V1 backend; set local_transcription.enabled: false"
        )
    channels = raw.get("channels")
    if not isinstance(channels, list):
        raise ConfigError("channels must be a list (use [] for collect-only mode)")
    parsed, names, urls = [], set(), set()
    for i, channel in enumerate(channels):
        channel = mapping(channel, f"channels[{i}]", ["name", "url"])
        name, url = channel.get("name"), channel.get("url")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"channels[{i}].name must be a nonempty string")
        if not isinstance(url, str):
            raise ConfigError(f"channels[{i}].url must be an HTTPS YouTube channel URL")
        u = urlparse(url)
        if (
            u.scheme != "https"
            or u.hostname not in YOUTUBE_HOSTS
            or u.netloc != u.hostname
            or u.query
            or u.fragment
            or not re.fullmatch(
                r"/(?:@[^/]+|(?:channel|c|user)/[^/]+)(?:/(?:videos|streams|shorts))?/?", u.path
            )
        ):
            raise ConfigError(f"channels[{i}].url must be an HTTPS YouTube channel URL")
        url = url.rstrip("/")
        if name.strip() in names or url in urls:
            raise ConfigError(f"Duplicate channel name or URL: {name}")
        names.add(name.strip())
        urls.add(url)
        parsed.append(Channel(name.strip(), url))
    return Config(
        output,
        db,
        tuple(parsed),
        **kwargs,
        preferred_languages=tuple(languages),
        max_attempts=attempts,
        backoff_hours=hours,
        rate_limit_cooldown_hours=cooldown,
    )
