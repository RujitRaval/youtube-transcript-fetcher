# Implementation plan and decisions

1. Validate YAML into small dataclasses; resolve storage paths relative to the config.
2. Discover only the most recent configured number of uploads using flat yt-dlp
   extraction. Persist video IDs before retrieval and retry independently of discovery.
3. Select existing English manual captions, then English automatic captions, then
   configured languages. Try youtube-transcript-api first, then yt-dlp subtitle data.
4. Save canonical timestamped JSON, Markdown, VTT, and metadata using atomic writes.
5. Track attempts and backoff in SQLite; serialize writers with a process lock and
   recover interrupted processing. Persist a global cooldown for blocked requests.
6. Add the CLI, doctor, native scheduler templates and a scheduler-file renderer.
7. Test without network, format/lint, build/install and smoke-test locally; attempt
   a separately identified live caption check.

## Decisions

- One synchronous process per cycle; no server, worker queue, threads, or daemon.
- SQLite `video_id` primary key is the deduplication boundary. A Unix advisory lock
  adjacent to the DB serializes mutating commands; a crashed process releases it.
- Store each file atomically and write metadata last as a checksum manifest. Recovery
  validates a complete bundle before marking complete without another network call.
  This is not a multi-file filesystem transaction: readers may briefly observe a
  mixture during an explicit forced refresh; validation rejects incomplete bundles.
- Freeze the relative output path on first write so later title/date changes or force
  do not create duplicate folders. Include the video ID and a channel-name hash.
- English is always preferred. Other languages are opt-in; no machine translation.
- Rate limiting or IP blocking stops the cycle and persists a cooldown. Do not use
  the second provider to evade an explicit block. Ordinary missing captions do use it.
- Missing captions eventually become `failed` with a specific error kind; explicit
  retry resets the budget. Private/deleted videos fail immediately. Network/application
  errors have bounded retries too. Provider failures during refresh leave existing
  files untouched; interruption during output replacement has the manifest caveat above.
- Metadata enrichment is best effort and does not prevent saving available captions.
  Unknown publication dates remain null and use an `unknown-date` folder.
- Local transcription is only a Python protocol seam in V1. Configuration requesting
  it is rejected with a clear explanation; no backend/audio downloader ships yet.
- Native Python packaging is the default. uv is an optional faster venv/pip substitute,
  not a runtime dependency. No automatic dependency updates in unattended runs.
- Linux/macOS are supported; native Windows scheduling/locking is outside V1.

## Upstream verification (2026-10-05)

- [youtube-transcript-api API](https://github.com/jdepoix/youtube-transcript-api):
  instance `list(video_id)`, transcript `fetch(preserve_formatting=True)`,
  `FetchedTranscript.to_raw_data()`, language code and generated/manual flags.
  [MIT license](https://github.com/jdepoix/youtube-transcript-api/blob/master/LICENSE).
- [yt-dlp embedding/options](https://github.com/yt-dlp/yt-dlp):
  `YoutubeDL.extract_info(download=False)`, `extract_flat`, `playlistend`,
  `subtitles` / `automatic_captions`; retrieve selected JSON3/VTT via `urlopen`.
  `skip_download=True` everywhere; no media downloader or ffmpeg is needed.
  PyPI source/wheel are [Unlicense](https://github.com/yt-dlp/yt-dlp/blob/master/LICENSE),
  unlike bundled standalone binaries which can incorporate GPL code. We depend on
  the Python package, not those binaries. PyYAML is MIT; requests is Apache-2.0.
- Current full yt-dlp YouTube support can require yt-dlp-ejs and a supported local
  JavaScript runtime. Caption-only extraction can still work without these, but
  availability is not guaranteed. No security bypasses, proxy rotation, cookies,
  sign-in automation, or downloading remote JS components is implemented.
- Both extractors depend on unofficial, changing YouTube interfaces. Captions may be
  delayed, disabled, restricted, or blocked. No permanent unattended guarantee is
  possible; provide actionable logs and dependency-upgrade instructions.
- [uv environments](https://docs.astral.sh/uv/pip/environments/) supports the same
  ordinary virtualenv model and speeds setup without changing the architecture.
