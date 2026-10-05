import logging
import time
import uuid
from datetime import datetime, timedelta

from .database import Database
from .output.storage import recover, relative_path, save
from .retry import RetrievalError, classify, next_retry
from .utils import iso, utcnow
from .youtube.discovery import discover
from .youtube.transcripts import retrieve
from .youtube.ytdlp import details, publication

log = logging.getLogger(__name__)


class Collector:
    """Caller holds writer_lock for the entire lifetime of a mutating operation."""

    def __init__(self, config, db: Database):
        self.config, self.db = config, db
        self.had_errors = False

    def cooling_down(self):
        until = self.db.setting("cooldown_until")
        return bool(until and datetime.fromisoformat(until) > utcnow())

    def cooldown(self):
        until = iso(utcnow() + timedelta(hours=self.config.rate_limit_cooldown_hours))
        self.db.setting("cooldown_until", until)
        log.warning("YouTube blocked/rate-limited requests; pausing all requests until %s", until)

    def fail(self, row, error):
        next_at = next_retry(self.config, row["attempt_count"], utcnow(), error.kind)
        self.db.update(
            row["video_id"],
            status="waiting_for_transcript" if next_at else "failed",
            next_attempt_at=iso(next_at) if next_at else None,
            error_kind=error.kind,
            error_message=str(error)[:2000],
        )
        log.warning(
            "%s: %s; %s",
            row["video_id"],
            error.kind,
            f"retry after {iso(next_at)}" if next_at else "failed (see status --details)",
        )
        log.debug("Failure details: %s", error)
        if error.kind == "rate_limited":
            self.cooldown()
        if error.kind != "transcript_unavailable":
            self.had_errors = True

    def complete(self, identifier, metadata):
        self.db.update(
            identifier,
            status="complete",
            next_attempt_at=None,
            error_message=None,
            error_kind=None,
            refresh_requested=0,
            transcript_source=metadata["transcript_source"],
            transcript_language=metadata["transcript_language"],
            processed_at=metadata["collected_at"],
        )
        log.info("Complete: %s", identifier)

    def process(self, row):
        identifier = row["video_id"]
        existing = recover(self.config.output_directory, row["output_path"], identifier)
        if existing and (
            not row["refresh_requested"] or existing.get("operation_id") == row["operation_id"]
        ):
            self.complete(identifier, existing)
            log.info("Recovered saved bundle without a network request: %s", identifier)
            return
        if row["status"] == "processing":
            self.fail(
                row, RetrievalError("interrupted", "Previous process exited during this attempt")
            )
            return
        self.db.update(
            identifier,
            status="processing",
            attempt_count=row["attempt_count"] + 1,
            last_attempt_at=iso(utcnow()),
            operation_id=uuid.uuid4().hex,
        )
        row = self.db.get(identifier)
        try:
            log.info("Checking YouTube transcript: %s — %s", identifier, row["title"])
            transcript = retrieve(identifier, self.config)
            info = transcript.metadata
            if not info and (row["published_at"] is None or row["duration_seconds"] is None):
                time.sleep(self.config.request_delay_seconds)
                try:
                    info = details(row["video_url"], self.config) or {}
                except Exception as exc:
                    error = classify(exc)
                    log.info("Saving captions with partial metadata: %s", error.kind)
                    if error.kind == "rate_limited":
                        self.cooldown()
            updates = {}
            if info:
                for key, value in {
                    "published_at": publication(info),
                    "duration_seconds": info.get("duration"),
                    "title": info.get("title"),
                }.items():
                    if value is not None:
                        updates[key] = value
                if row["channel_name"] == "manual" and info.get("channel"):
                    updates["channel_name"] = info["channel"]
                    updates["channel_url"] = info.get("channel_url") or ""
            if updates:
                self.db.update(identifier, **updates)
                row = self.db.get(identifier)
            relative = row["output_path"] or str(relative_path(row))
            self.db.update(identifier, output_path=relative)
            metadata = save(self.config.output_directory, relative, row, transcript)
            self.complete(identifier, metadata)
        except Exception as exc:
            log.debug("Collection exception", exc_info=True)
            self.fail(row, classify(exc))

    def run(self):
        if self.cooling_down():
            log.info(
                "Cooldown active until %s; no YouTube requests", self.db.setting("cooldown_until")
            )
            return
        for channel in self.config.channels:
            log.info("Checking %s", channel.name)
            try:
                videos = discover(channel, self.config)
                known = sum(self.db.get(video.video_id) is not None for video in videos)
                for video in videos:
                    self.db.insert(video)
                log.info("Found %d recent videos; %d already known", len(videos), known)
            except Exception as exc:
                error = classify(exc)
                self.had_errors = True
                log.warning("Channel %s: %s: %s", channel.name, error.kind, error)
                if error.kind == "rate_limited":
                    self.cooldown()
                    return
            time.sleep(self.config.request_delay_seconds)
        # Include waiting videos that have aged out of the recent upload window.
        for row in self.db.due(utcnow(), self.config.max_videos_per_run):
            self.process(row)
            if self.cooling_down():
                break
            time.sleep(self.config.request_delay_seconds)
