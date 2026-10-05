from datetime import datetime, timedelta

from .config import Config


class RetrievalError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def classify(exc: Exception) -> RetrievalError:
    if isinstance(exc, RetrievalError):
        return exc
    name, message = type(exc).__name__, str(exc)
    text = message.lower()
    if name in {"RequestBlocked", "IpBlocked", "TooManyRequests"} or any(
        x in text
        for x in (
            "429",
            "too many requests",
            "not a bot",
            "confirm you’re not a bot",
            "confirm you’re",
            "ip blocked",
            "request blocked",
        )
    ):
        kind = "rate_limited"
    elif name in {"VideoUnavailable", "InvalidVideoId", "AgeRestricted", "VideoUnplayable"} or any(
        x in text
        for x in (
            "private video",
            "video has been removed",
            "video unavailable",
            "members-only",
            "sign in to confirm your age",
        )
    ):
        kind = "unavailable"
    elif name in {"TranscriptsDisabled", "NoTranscriptFound", "NotTranslatable"}:
        kind = "transcript_unavailable"
    elif any(
        x in name.lower() for x in ("connection", "timeout", "http", "request", "transport")
    ) or any(
        x in text
        for x in ("timed out", "unable to download", "network", "name resolution", "http error")
    ):
        kind = "network"
    else:
        kind = "unexpected"
    return RetrievalError(kind, f"{name}: {message}"[:2000])


def next_retry(config: Config, attempt: int, now: datetime, kind: str) -> datetime | None:
    if kind == "unavailable" or attempt >= config.max_attempts:
        return None
    delay = config.backoff_hours[min(max(attempt - 1, 0), len(config.backoff_hours) - 1)]
    if kind == "rate_limited":
        delay = max(delay, config.rate_limit_cooldown_hours)
    return now + timedelta(hours=delay)
