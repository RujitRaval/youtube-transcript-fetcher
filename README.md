# YouTube Transcript Collector

A small, self-hosted Python utility that watches a handful of YouTube channels and
saves their existing captions as timestamped JSON, readable Markdown, and WebVTT.
Install it once on a family member's computer, configure their channels, and let
macOS launchd or Linux systemd invoke it roughly hourly. Each invocation exits.

**Local storage. No analytics, telemetry, tracking, automatic uploads, paid APIs,
API keys, LLMs, account login, or video/audio downloads.** Network access to YouTube
is necessary; there is no hosted application or third-party transcript service.
YouTube can see your IP address and requested videos. Caption access depends on
YouTube and sometimes requires a developer to update the extraction libraries.

## How it works

```text
Configured channels → bounded recent-upload discovery (yt-dlp)
                    → SQLite video ID deduplication + due retry queue
                    → youtube-transcript-api → yt-dlp subtitle fallback
                    → raw JSON + Markdown + VTT + metadata → complete
```

Within each provider: manual English → automatic English → configured additional
languages (manual before automatic within each language). English regional codes
and `en-orig` are supported. Machine-translated subtitle URLs are excluded. An
explicit rate limit/IP block pauses all YouTube requests rather than trying another
provider. Ordinary missing captions or provider failures do use the fallback.

There is no daemon loop, web UI, queue service, Docker requirement, or YouTube Data
API. No local transcription backend is included in V1; the extension interface is
in `transcription/base.py`. Enabling local transcription currently gives a useful
configuration error instead of silently pretending to transcribe.

## Requirements and installation

- Linux or macOS, Python **3.11+**, internet access, a local writable disk.
- No ffmpeg, GPU, or audio model required for this caption-only workflow.
- Run setup as the account that will own the transcripts; do not use `sudo pip`.

Clone the repository (or extract a downloaded source archive):

```sh
git clone https://github.com/RujitRaval/youtube-transcript-fetcher.git
```

Then install from the project directory:

```sh
cd youtube-transcript-fetcher  # or the directory name of your clone
python3 --version             # must be 3.11 or newer
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
cp config.example.yaml config.yaml
```

Edit `config.yaml`: replace the example creators with real YouTube channel names
and URLs. Add about five entries. The examples are placeholders, not recommended
subscriptions. Save this directory somewhere stable in your home directory (for
example `~/Applications/youtube-transcript-collector` on macOS). Moving the project
or virtualenv after scheduling requires regenerating and reinstalling the job.

```sh
youtube-transcript-collector doctor
youtube-transcript-collector doctor --network
youtube-transcript-collector run
youtube-transcript-collector status --details
```

If Python is missing, install a supported Python from [python.org](https://www.python.org/downloads/).
On Debian/Ubuntu the distribution's `python3-venv` package may also be necessary.
The first run collects up to the recent-video limit per channel, **including existing
recent uploads**, subject to the per-run processing limit. It does not backfill an
entire channel. Open `transcripts/` after a successful run, then install a scheduler
below. Your family member does not need to activate the environment or run commands.

### Optional uv installation

If you already use [uv](https://docs.astral.sh/uv/), it makes environment creation
and dependency installation faster while keeping the same conventional `.venv`:

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .
cp config.example.yaml config.yaml
.venv/bin/youtube-transcript-collector doctor
```

Choose either pip or uv; neither uv nor a package manager runs during scheduled
collection. There are no automatic dependency upgrades. Install fresh dependencies
when needed with `python -m pip install --upgrade -e .`, then run doctor and a sample
collection. Back up state and output before upgrading the collector itself.

## Configuration

See [config.example.yaml](config.example.yaml) for every option. Unknown keys and
invalid values are rejected. Paths resolve relative to the YAML file, independent
of the scheduler's working directory. `~` is supported; shell variables such as
`$HOME` are not expanded. Absolute paths are also accepted.

```yaml
storage:
  output_directory: ./transcripts
  database: ./data/collector.db
collector:
  recent_video_limit: 10
  max_videos_per_run: 20
  request_delay_seconds: 2
  network_timeout_seconds: 20
transcripts:
  preferred_languages: [en, en-orig, es]
  retry:
    max_attempts: 6
    backoff_hours: [1, 3, 12, 24, 48]
    rate_limit_cooldown_hours: 6
local_transcription:
  enabled: false
channels:
  - name: favorite-creator
    url: https://www.youtube.com/@REPLACE_WITH_REAL_HANDLE
```

Use a unique name and URL for each channel. `/channel/UC…`, `/c/…`, and `/user/…`
URLs also work. Channel roots use the `/videos` tab. Explicit `/streams` or `/shorts`
URLs select that tab instead. Discovery skips currently live/upcoming entries.
To include multiple tabs, add entries with distinct names and URLs; video IDs remain
unique across the whole database. Use `channels: []` for manual collection only.

`recent_video_limit` bounds each channel discovery; `max_videos_per_run` bounds
processing per invocation. Initial defaults may take multiple hourly runs to drain
five channels' backlog. Retries continue even after a video leaves the discovery
window. Requests are sequential, with delays between channel/video/provider steps;
libraries may make several HTTP requests per step. Keep delays conservative.

## Commands

Global options **precede** the command:

```sh
youtube-transcript-collector --config /absolute/path/config.yaml run
youtube-transcript-collector status
youtube-transcript-collector status --details
youtube-transcript-collector channels
youtube-transcript-collector collect 'https://www.youtube.com/watch?v=arj7oStGLkU'
youtube-transcript-collector collect 'https://www.youtube.com/watch?v=arj7oStGLkU' --force
youtube-transcript-collector retry-failed
youtube-transcript-collector --verbose run
youtube-transcript-collector --log-file logs/collector.log run
python -m youtube_transcript_collector  # defaults to one run
```

`collect` also accepts a bare video ID. Completed videos are skipped, failed videos
require an explicit retry, and waiting videos honor their retry time. `--force`
resets that video's retry budget and refreshes its files at the same path, but does
not override a global YouTube cooldown. `retry-failed` only resets failed rows;
run `run` afterward to process them. It does not erase saved files.

Exit codes: `0` for a handled cycle (including ordinary waiting captions or a
scheduled cooldown skip), `1` for collection/discovery errors or a failed manual
collection, `2` for config/environment/CLI errors, `130` for interruption. `status`
is the authoritative view of per-video failures; use `--details` for error reasons.
`doctor` checks versions, config, writable storage and SQLite integrity. Add
`--network` for a connectivity request; connectivity alone cannot prove captions work.

## Retries, idempotency, and recovery

SQLite initializes itself; the schema is versioned with `PRAGMA user_version`.
A video ID is the primary key. Completed videos are never re-fetched by `run`.
The lifecycle is `discovered → processing → complete`, or
`processing → waiting_for_transcript → processing`, eventually `failed`.

The default six attempts use delays of 1, 3, 12, 24, and 48 hours (88 hours total
from first to last attempt, plus scheduler delays). Further configured attempts
reuse the final delay. Missing/disabled captions, network errors, and unexpected
errors are recorded separately. Private/deleted/restricted videos fail immediately.
HTTP 429/IP blocks impose a persistent cooldown of at least six hours; no proxy,
cookie, or security-bypass mechanism is used.

An advisory lock next to the DB prevents overlapping **mutating** commands sharing
that DB. Use one DB/output pair per installation and keep both on local disk.
Interrupted attempts recover a checksummed saved bundle without a network call,
or schedule another attempt with backoff. The DB is marked complete only after
all output files have been saved. Each file is written to a temporary file, flushed,
and atomically replaced; metadata is the final checksum manifest. A forced refresh
is not an atomic transaction across every file: a crash during replacement can leave
a mixed bundle until retry repairs it. Individual files are never partly overwritten.
Complete rows skip output integrity checks in normal operation; after manually
removing/damaging files, use `collect URL --force` to rebuild them.

## Output format

```text
transcripts/
  favorite-creator-<name-hash>/
    2026/
      2026-10-04-video-title-<video-id>/
        metadata.json
        transcript.raw.json
        transcript.md
        transcript.vtt
        transcript.original.json3  # when supplied by yt-dlp (or .vtt)
```

The channel hash distinguishes names with identical slugs. The video ID prevents
title collisions. Paths are fixed on first write, even if a title later changes.
Unknown dates use `unknown-date`; unavailable metadata stays null rather than
inventing a publication date. Metadata enrichment is best effort.

`transcript.raw.json` is the canonical list of `{start, duration, text}` segments,
with numeric times in seconds. Markdown and generated VTT derive from it. The
primary provider preserves its returned text/formatting. For yt-dlp, the original
JSON3/VTT payload is saved separately, and JSON3 events or VTT cues are normalized
to segments. Original VTT is preserved when available. Otherwise VTT is generated
from timestamps (zero-duration events receive a 1 ms display cue). Formatting,
rolling caption repetition and overlaps are not editorially rewritten. Word-level
JSON3 details remain available in the original payload.

`metadata.json` records video/channel identity, URL, title, publication, duration,
provider, transcript source/language, collection time, schema version and file
checksums. A successful forced refresh may leave an older `transcript.original.*`
file from the other provider; only files in the current manifest belong to that
bundle. Do not edit canonical outputs if you rely on recovery validation.

## macOS: launchd (recommended for your wife's Mac)

After installation, editing config, and one successful foreground collection,
run these as **her normal macOS account** from the project directory:

```sh
.venv/bin/python scripts/render_scheduler.py --platform launchd --config config.yaml
plutil -lint build/scheduler/com.youtube-transcript-collector.plist
mkdir -p "$HOME/Library/LaunchAgents"
cp build/scheduler/com.youtube-transcript-collector.plist "$HOME/Library/LaunchAgents/"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.youtube-transcript-collector.plist"
launchctl kickstart "gui/$(id -u)/com.youtube-transcript-collector"
launchctl print "gui/$(id -u)/com.youtube-transcript-collector"
```

The renderer uses the exact virtualenv interpreter, absolute config/project/log
paths, and XML escaping. It only writes files; it does not install anything itself.
The LaunchAgent runs at login and roughly hourly while logged in. After a reboot,
it resumes **when she logs in**; it does not run before login or while powered off.
Sleeping computers cannot collect until awake. Keep the project outside protected
Desktop/Documents folders if macOS denies background access. With FileVault,
normal login/unlock is required. There is no persistent Python process.

Logs rotate at `logs/collector.log` beside the config (5 MB, three backups). Inspect
that file and `status --details` if files stop arriving. Keep the config and virtualenv
in place. Do not enable `KeepAlive` for this one-shot job.

To remove the job (keeps all transcripts and state):

```sh
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.youtube-transcript-collector.plist"
rm "$HOME/Library/LaunchAgents/com.youtube-transcript-collector.plist"
```

To update job paths, boot it out, regenerate/copy the plist, then bootstrap again.

## Linux: systemd user timer

Run under the account that owns the project:

```sh
.venv/bin/python scripts/render_scheduler.py --platform systemd --config config.yaml
mkdir -p "$HOME/.config/systemd/user"
cp build/scheduler/youtube-transcript-collector.{service,timer} "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now youtube-transcript-collector.timer
systemctl --user start youtube-transcript-collector.service
systemctl --user list-timers youtube-transcript-collector.timer
journalctl --user -u youtube-transcript-collector.service -n 50
```

The timer runs hourly with up to five minutes of random delay. `Persistent=true`
catches a missed calendar run when the user manager starts again. To start the user
manager at boot and continue while logged out, enable lingering (may require admin
approval):

```sh
sudo loginctl enable-linger "$USER"
```

Without lingering, collection resumes at login. A sleeping/offline machine cannot
collect; network failures retry later. The service has a 45-minute safety timeout;
interrupted processing is recovered on a later run. Logs also rotate beside config.

Remove the timer and service (keeps data):

```sh
systemctl --user disable --now youtube-transcript-collector.timer
systemctl --user stop youtube-transcript-collector.service
rm "$HOME/.config/systemd/user/youtube-transcript-collector.service"
rm "$HOME/.config/systemd/user/youtube-transcript-collector.timer"
systemctl --user daemon-reload
```

Disable lingering only if no other user services need it:
`sudo loginctl disable-linger "$USER"`.

### Optional cron alternative

Use `crontab -e` and absolute paths, replacing these placeholders:

```cron
7 * * * * /ABS/PROJECT/.venv/bin/python -m youtube_transcript_collector --config /ABS/PROJECT/config.yaml --log-file /ABS/PROJECT/logs/collector.log run >/dev/null 2>&1
```

Remove that line to uninstall. Quote paths containing spaces; cron treats `%`
specially, so prefer systemd if your paths contain it. Cron must be enabled at boot.
Do not install both cron and a native timer for the same collector.

## Troubleshooting and limitations

- **No transcripts yet:** inspect `status --details`. Captions can be delayed,
  disabled, or absent in your preferred languages. The collector cannot create
  missing captions in V1. Try a known captioned public video with `collect`.
- **Blocked/429/bot check:** leave the cooldown in place. The utility does not try
  to defeat restrictions. Some networks/IPs are blocked even at low volume.
- **Extraction suddenly fails:** activate `.venv`, run
  `python -m pip install --upgrade yt-dlp youtube-transcript-api`, then doctor and
  `collect URL --force`. Dependencies use unofficial interfaces that can change.
- **JavaScript runtime/PO-token/authentication warnings:** current full yt-dlp
  YouTube support may need `yt-dlp-ejs` and a supported local JS runtime; see
  [upstream EJS guidance](https://github.com/yt-dlp/yt-dlp/wiki/EJS). This application
  does not install runtimes, fetch remote JS components, automate token acquisition,
  or provide cookie authentication. Restricted videos can remain unavailable.
  The primary caption API does not use a JavaScript runtime.
- **Network/DNS/TLS error:** check `doctor --network`, network access and the system
  clock/certificate configuration. Do not disable TLS verification. State is retained.
- **Permission/path error:** use `doctor` with the same account/config as the job.
  `--config` precedes the subcommand. Avoid OS-protected or network-mounted folders.
- **Another collector running:** wait for it to finish. The lock releases on exit or
  process death; deleting its file while a process holds it can defeat locking.
- **Disk full:** free disk space and retry. The SQLite DB and output are both needed.
  Pause the scheduler before backing up `config.yaml`, `data/` (including any WAL
  sidecars), and `transcripts/` together; copying only the DB during writes is unsafe.
- **Long offline period/busy channels:** discovery sees only the newest N entries per
  configured tab. Uploads older than that window can be missed. Increase the limit
  temporarily (maximum 1000) or collect known video URLs. This is not a full archive.
- Caption quality, language tags, rolling repetition, metadata availability and
  upstream access cannot be guaranteed. No speaker diarization or summaries.
- Linux/macOS only in V1. Scheduled jobs need the computer awake and the appropriate
  account session/user manager running. Scheduler installation is explicit.
- Use transcripts in accordance with applicable rights and YouTube's terms. The MIT
  license covers this software, not ownership of collected transcripts.

## Development, architecture, and next steps

```sh
python -m pip install -e '.[dev]'
ruff format --check .
ruff check .
pytest
python -m build
```

Tests block network and mock YouTube. GitHub Actions defines Linux/macOS jobs across
Python 3.11–3.14; local validation does not imply that matrix has run. See
[docs/validation.md](docs/validation.md) for what was actually checked.

```text
src/youtube_transcript_collector/
  cli.py, config.py, collector.py, database.py, models.py, retry.py, utils.py
  youtube/          # bounded discovery + both caption providers
  output/           # atomic storage, Markdown, VTT
  transcription/    # future local backend protocol only
tests/              # deterministic offline tests
scripts/            # scheduler renderer
deploy/             # launchd/systemd templates
docs/               # plan, decisions, validation
```

[Architecture decisions and upstream research](docs/architecture.md) explain the
tradeoffs. The project uses MIT; youtube-transcript-api is MIT and the yt-dlp PyPI
package is Unlicense. Dependency licenses still apply. See [CONTRIBUTING.md](CONTRIBUTING.md)
and [SECURITY.md](SECURITY.md).

Reasonable V2 work: an opt-in faster-whisper/whisper.cpp backend after caption retry
exhaustion, a lightweight health notification, SQLite full-text search over saved
segments, and improved gap/backfill discovery. Diarization, embeddings, summaries,
and a UI should remain separate optional additions.
