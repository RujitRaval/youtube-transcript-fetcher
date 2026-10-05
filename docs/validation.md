# Validation — 2026-10-05

## Environment actually exercised

- macOS, Python 3.12.13 in the project virtualenv.
- youtube-transcript-api 1.2.4; yt-dlp 2026.8.19; PyYAML 6.0.3;
  requests 2.34.2; pytest 9.1.1; Ruff 0.16.10.
- Normal editable installation with uv's pip interface and isolated build succeeded.
  An initial offline, non-isolated build lacked Hatchling's dynamic `editables`
  dependency; the documented standard isolated build resolved it successfully.

## Checks completed

- `ruff format --check .`: passed.
- `ruff check .`: passed.
- `pytest -q`: **66 passed**, with socket connections blocked by the test fixture.
- Source distribution and wheel build: passed. Wheel has the console entry point;
  source archive includes README, example config, scheduler renderer and templates.
  Build artifacts exclude local state, transcripts and virtualenv.
- Clean standard-pip installation from the source archive: passed in a separate
  virtualenv created with `python -m venv`; installed CLI version and doctor passed.
- CLI smoke: version, doctor, no-channel run, status, channels, retry-failed,
  manual collection, explicit force, and `python -m` entry point checked (some
  collection/force cases use mocked providers in the CLI tests).
- launchd plist rendered with absolute paths and checked using `plutil -lint`: passed.
- systemd service/timer rendered and checked by unit tests for substitutions and
  persistence settings. Not executed under a Linux systemd manager.

## Live YouTube checks (separate from the test suite)

1. `doctor --network`: YouTube connectivity succeeded.
2. Collected `https://www.youtube.com/watch?v=arj7oStGLkU`:
   “Inside the Mind of a Master Procrastinator | Tim Urban | TED”.
   youtube-transcript-api retrieved **315 manual English segments**. yt-dlp metadata
   enrichment supplied title, channel, publication and duration. Wrote metadata,
   canonical JSON, Markdown and VTT; all manifest checksums validated.
3. Repeated `collect` for that video: skipped the completed row without network.
4. Flat discovery of TED's channel, limited to one video: returned one recent upload.
5. Direct yt-dlp subtitle fallback for the same public video: retrieved **315 manual
   English segments** from JSON3 without downloading audio/video.

The live smoke artifacts are in ignored `build/smoke/` in this working copy. They
are not shipped in the source distribution. Neither live path required a JS runtime
installation, cookies, account login, or credentials here. This does not guarantee
that YouTube grants the same access from another network or for other videos.
Automatic captions, English variants, preferred-language ranking, and fallback VTT
are covered with mocked upstream responses; the live checks above used manual captions.

## Not claimed as tested

- No scheduler was registered, no machine reboot was performed, and no unattended
  multi-day collection was observed. launchd syntax is validated, not reboot behavior.
- Linux execution and Python 3.11/3.13/3.14 are represented in the CI matrix but have
  not been run locally. GitHub Actions has not been executed for this unpublished tree.
- No local transcription implementation exists in V1.

## Reproduce

```sh
. .venv/bin/activate
python -m pip install -e '.[dev]'
ruff format --check .
ruff check .
pytest -q
python -m build
cp config.example.yaml config.yaml  # only if you have not created a config
# Edit the channel entries before run; use channels: [] for a manual-only test.
youtube-transcript-collector doctor --network
youtube-transcript-collector collect 'https://www.youtube.com/watch?v=arj7oStGLkU'
youtube-transcript-collector status --details
youtube-transcript-collector run
```

If a previous attempt is complete, a second command is deliberately a skip. To
explicitly recheck providers use `collect URL --force`; global cooldowns still apply.
