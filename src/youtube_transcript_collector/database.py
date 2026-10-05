import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .models import Video
from .utils import iso, utcnow


@contextmanager
def writer_lock(database: Path):
    """Serialize whole cycles, including network and filesystem work; auto-release on crash."""
    import fcntl

    database.parent.mkdir(parents=True, exist_ok=True)
    with database.with_suffix(database.suffix + ".lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                "Another collector is running for this database; try again later"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version > 1:
            self.connection.close()
            raise RuntimeError("Database is newer than this collector; upgrade the application")
        self.connection.execute("PRAGMA journal_mode=WAL")
        with self.connection:
            self.connection.execute("""CREATE TABLE IF NOT EXISTS videos (
                video_id TEXT PRIMARY KEY, channel_name TEXT NOT NULL, channel_url TEXT NOT NULL,
                title TEXT NOT NULL, video_url TEXT NOT NULL,
                published_at TEXT, duration_seconds REAL,
                status TEXT NOT NULL DEFAULT 'discovered' CHECK(status IN
                    ('discovered','waiting_for_transcript','processing','complete','failed')),
                transcript_source TEXT, transcript_language TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_attempt_at TEXT, next_attempt_at TEXT,
                discovered_at TEXT NOT NULL, processed_at TEXT, error_message TEXT, error_kind TEXT,
                output_path TEXT, operation_id TEXT, refresh_requested INTEGER NOT NULL DEFAULT 0
            )""")
            self.connection.execute("""CREATE INDEX IF NOT EXISTS videos_due
                ON videos(status, next_attempt_at)""")
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
            )
            self.connection.execute("PRAGMA user_version=1")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.connection.close()

    def insert(self, video: Video):
        with self.connection:
            self.connection.execute(
                """INSERT OR IGNORE INTO videos
                (video_id,channel_name,channel_url,title,video_url,published_at,duration_seconds,discovered_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (*vars(video).values(), iso(utcnow())),
            )

    def get(self, video_id):
        return self.connection.execute(
            "SELECT * FROM videos WHERE video_id=?", (video_id,)
        ).fetchone()

    def update(self, video_id, **fields):
        allowed = {r[1] for r in self.connection.execute("PRAGMA table_info(videos)")} - {
            "video_id"
        }
        if not fields.keys() <= allowed:
            raise ValueError("Unknown database field")
        with self.connection:
            self.connection.execute(
                f"UPDATE videos SET {','.join(f'{key}=?' for key in fields)} WHERE video_id=?",
                (*fields.values(), video_id),
            )

    def due(self, now, limit):
        return self.connection.execute(
            """SELECT * FROM videos WHERE
            status IN ('discovered','waiting_for_transcript','processing')
            AND (next_attempt_at IS NULL OR next_attempt_at<=?)
            ORDER BY COALESCE(next_attempt_at,discovered_at), video_id LIMIT ?""",
            (iso(now), limit),
        ).fetchall()

    def retry_failed(self):
        with self.connection:
            result = self.connection.execute("""UPDATE videos SET status='discovered',
                attempt_count=0,next_attempt_at=NULL,error_message=NULL,error_kind=NULL
                WHERE status='failed'""")
        return result.rowcount

    def setting(self, key, value=None):
        if value is not None:
            with self.connection:
                self.connection.execute(
                    "INSERT OR REPLACE INTO settings VALUES (?,?)", (key, value)
                )
        row = self.connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def counts(self):
        return dict(self.connection.execute("SELECT status, COUNT(*) FROM videos GROUP BY status"))
