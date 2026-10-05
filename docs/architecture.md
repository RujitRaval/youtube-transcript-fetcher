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

- One synchronous process per caption cycle; no worker queue or daemon. The explicit
  speaker review command starts a separate localhost HTTP server.
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
  it is rejected with a clear explanation. Optional speaker diarization analyzes audio
  without transcribing words and does not use this setting.
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
  `skip_download=True` in caption collection; no media downloader or ffmpeg is needed
  for that path. Explicit speaker analysis uses audio-only download and local FFmpeg.
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

## Speaker analysis (0.2.0)

- Explicit `diarize` command; default runs/schedulers never download audio or models.
- Optional dependencies are imported lazily. Setup downloads pinned, checksum-verified
  public model assets. Extraction reads only named regular files from the archive.
- Diarization generates anonymous voice clusters, numbered by first occurrence.
  Caption alignment uses interval overlap; mixed cues are flagged without splitting words.
- Each run stages into a temporary directory and publishes through an atomic current
  pointer only after model results, playback audio, and initial edits are saved.
- Names and cue overrides share one atomic edits document with their before/after
  history. Optimistic run/revision checks reject stale saves. All writes hold the
  existing collector lock. Caption hashes reject results made against older captions.
- The review server binds only to 127.0.0.1, serves a fixed route allowlist, validates
  Host and Origin, and requires an unpredictable per-server edit token. No CORS.
  User text is rendered through textContent; a restrictive CSP blocks inline scripts.
- Audio range requests support timestamp seeking. Playback/export links are bound to
  the loaded run ID. Exports use current saved names while preserving original model
  evidence and edit history in JSON. No identity inference, cloud service, or telemetry.

## Refining speaker sections (0.3.0)

- `speakers refine` uses the current run's verified PCM16 audio. It clips a temporary
  analysis section at the nearest audio frame and invokes the existing backend
  with an explicit count. No audio download is attempted.
- Turns before the boundary retain their cluster IDs; crossing turns are clipped.
  Later clusters use a distinct namespace, then all IDs are normalized by first
  occurrence. Whole earlier caption assignments are retained; boundary and later
  cues are aligned against the combined turns. This does not match a person
  across the boundary.
- New runs record parent run/revision and accumulated human constraints. Names and
  manual corrections start fresh; all previous edits remain in their parent run.
  Full audio and original caption text/timestamps are preserved.
- Analysis, copy verification, run data and exports finish before the atomic
  current pointer changes. Any failure retains the previous current run.
