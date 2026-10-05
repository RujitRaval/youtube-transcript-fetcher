import argparse
import logging
import sqlite3
import sys
import tempfile
from datetime import datetime
from importlib.metadata import version
from logging.handlers import RotatingFileHandler
from pathlib import Path

import requests

from . import __version__
from .collector import Collector
from .config import ConfigError, load_config
from .database import Database, writer_lock
from .models import Video
from .utils import utcnow, video_id, watch_url


def parser():
    root = argparse.ArgumentParser(
        description="Collect YouTube captions locally, one cycle at a time."
    )
    root.add_argument("--config", type=Path, default=Path("config.yaml"))
    root.add_argument("--verbose", "-v", action="store_true")
    root.add_argument("--log-file", type=Path, help="Optional rotating log (5 MB, 3 backups)")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command")
    commands.add_parser("run", help="Discover recent uploads and process due videos once (default)")
    status = commands.add_parser("status", help="Show counts and retry state")
    status.add_argument("--details", action="store_true")
    commands.add_parser("channels", help="List configured channels")
    commands.add_parser("retry-failed", help="Reset failed videos; the next run processes them")
    collect = commands.add_parser("collect", help="Collect one video; respects retry times")
    collect.add_argument("video_url")
    collect.add_argument(
        "--force", action="store_true", help="Refresh even if complete; resets retry budget"
    )
    doctor = commands.add_parser("doctor", help="Check local installation and permissions")
    doctor.add_argument(
        "--network", action="store_true", help="Also make one YouTube connectivity request"
    )
    return root


def doctor(config, network):
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    for package in ("yt-dlp", "youtube-transcript-api", "PyYAML"):
        print(f"{package}: {version(package)}")
    print("Config: valid")
    config.output_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=config.output_directory) as handle:
        handle.write(b"permission check")
        handle.flush()
    print(f"Output writable: {config.output_directory}")
    with writer_lock(config.database), Database(config.database) as db:
        result = db.connection.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"SQLite check failed: {result}")
        db.setting("doctor_checked_at", utcnow().isoformat())
    print(f"Database readable/writable: {config.database}")
    if network:
        with requests.get(
            "https://www.youtube.com/robots.txt",
            timeout=config.network_timeout_seconds,
            stream=True,
        ) as response:
            response.raise_for_status()
        print("YouTube reachable (this does not guarantee captions are accessible)")
    else:
        print("Network: not checked; add --network to test connectivity")
    print("V1 local transcription: disabled. Full yt-dlp extraction may need a local JS runtime;")
    print("see README troubleshooting if caption extraction fails.")
    return 0


def execute(args, config):
    command = args.command or "run"
    if command == "doctor":
        return doctor(config, args.network)
    if command == "channels":
        for channel in config.channels:
            print(f"{channel.name}: {channel.url}")
        return 0
    if command == "status":
        with Database(config.database) as db:
            counts = db.counts()
            print(f"Channels: {len(config.channels)}\nVideos discovered: {sum(counts.values())}")
            for state in (
                "discovered",
                "processing",
                "complete",
                "waiting_for_transcript",
                "failed",
            ):
                print(f"{state}: {counts.get(state, 0)}")
            if db.setting("cooldown_until"):
                print(f"Last cooldown deadline: {db.setting('cooldown_until')}")
            if args.details:
                rows = db.connection.execute("""
                    SELECT video_id,status,attempt_count,next_attempt_at,error_kind,error_message
                    FROM videos WHERE status != 'complete' ORDER BY discovered_at
                    """)
                for row in rows:
                    print(dict(row))
        return 0
    with writer_lock(config.database), Database(config.database) as db:
        if command == "retry-failed":
            print(f"Reset {db.retry_failed()} failed videos; run collection to retry")
            return 0
        collector = Collector(config, db)
        if command == "run":
            collector.run()
            return 1 if collector.had_errors else 0
        identifier = video_id(args.video_url)
        if collector.cooling_down():
            print(f"Cooldown active until {db.setting('cooldown_until')}; try later")
            return 1
        db.insert(Video(identifier, "manual", "", identifier, watch_url(identifier)))
        row = db.get(identifier)
        if args.force:
            db.update(
                identifier,
                status="discovered",
                attempt_count=0,
                next_attempt_at=None,
                refresh_requested=1,
                operation_id=None,
                error_message=None,
                error_kind=None,
            )
            row = db.get(identifier)
        elif row["status"] in {"complete", "failed"}:
            print(f"{identifier}: {row['status']}; use --force to collect again")
            return 0 if row["status"] == "complete" else 1
        elif row["next_attempt_at"] and datetime.fromisoformat(row["next_attempt_at"]) > utcnow():
            print(f"{identifier}: retry scheduled for {row['next_attempt_at']}")
            return 0
        collector.process(row)
        state = db.get(identifier)["status"]
        return 1 if collector.had_errors or state == "failed" else 0


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        handlers = [logging.StreamHandler()]
        if args.log_file:
            args.log_file.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(
                RotatingFileHandler(
                    args.log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
                )
            )
        logging.basicConfig(
            level=logging.DEBUG if args.verbose else logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=handlers,
            force=True,
        )
        # HTTP libraries can expose signed URLs at debug level; restrict them even in verbose mode.
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        return execute(args, load_config(args.config))
    except (
        ConfigError,
        ValueError,
        OSError,
        RuntimeError,
        sqlite3.Error,
        requests.RequestException,
    ) as exc:
        logging.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        logging.warning("Interrupted; the next run will recover saved files or schedule a retry")
        return 130
