# Changelog

## 0.2.0 — 2026-10-05

- Optionally label recurring voices as Speaker 1, Speaker 2, and so on using local audio analysis.
- Rename speakers and correct individual captions in a local review page with timestamped playback.
- Save names and corrections on disk with edit history; export labeled Markdown, VTT, and audit JSON.
- Preserve original captions and past analysis runs; reject stale edits and incomplete model downloads.
- Keep caption collection and scheduled runs free of audio downloads and model dependencies.

## 0.1.0 — 2026-10-05

- Initial caption-first local collector with bounded yt-dlp discovery.
- Manual/automatic caption selection, timestamped JSON, Markdown and VTT exports.
- SQLite state, retries, global cooldown, overlap locking and bundle recovery.
- CLI diagnostics, one-video collection, status and explicit retry/refresh commands.
- Linux systemd and macOS launchd templates with an absolute-path renderer.
- Offline tests and native Python packaging; local transcription interface only.
