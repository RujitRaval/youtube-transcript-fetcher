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
- `pytest -q`: **140 passed**, with socket connections blocked by the test fixture.
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
- GitHub Actions [run 37354725723](https://github.com/RujitRaval/youtube-transcript-fetcher/actions/runs/37354725723)
  passed all eight Linux/macOS jobs across Python 3.11–3.14 for commit `a142e1e`.
  Each job installed the core/dev dependencies, checked formatting and lint,
  ran the offline tests, and built the package.

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

## Speaker labeling (v0.2.0)

- Installed the optional `speakers` dependencies, downloaded both pinned models,
  verified their SHA-256 hashes, and ran the actual CPU pipeline on video
  `SfOaZIGJ_gs` (Sam Altman x Nikhil Kamath).
- Processed the full **2,711.011-second** audio, covering **825 captions**.
  Inference took **256.69 seconds** on this machine. Automatic clustering produced
  **10 voice clusters**, with **27 captions flagged for review**. These are model
  groups, not a verified count of people; the same person can appear in multiple
  groups. No names were inferred.
- Browser QA on an isolated copy verified global rename, persistence after reload,
  caption correction, the review filter, audio playback/seek, and audit JSON
  download. The downloaded file contained both saved revisions with before/after
  values. Test edits did not modify the user's analysis.
- Added 44 offline tests for model integrity, alignment, immutable run publication,
  failure recovery, optimistic edit conflicts, CLI flows, HTTP access controls,
  audio range requests and exports. Default commands do not import audio packages.
- `node --check` passed for the review script; the built wheel includes its HTML,
  CSS and JavaScript resources. Audio, models, transcripts and local edits remain
  ignored by Git.

## Section refinement (v0.3.0)

- Added 30 offline tests covering exact PCM frame cropping, unchanged earlier
  assignments, stable IDs for overlapping turns, distinct later voices, preserved
  parent edits, provenance, exports, CLI arguments and failed-run recovery.
- The supported `speakers refine` CLI ran on an isolated copy of the full
  `SfOaZIGJ_gs` recording with `--from-seconds 43 --num-speakers 2`. Inference took
  **257.49 seconds**. Its turns and all **825 caption entries** exactly matched the
  previously reviewed correction: **four speakers overall**, only Speaker 3/4
  after 00:43, and **24 captions flagged for review**. This is reproduction of a
  reviewed result, not a measured speaker-accuracy score.
- The user's active review run, names and edits were not modified by the release
  verification. All live artifacts remain in ignored local directories.
- Created a new Python 3.12 virtualenv with standard `python -m venv`, installed
  the source archive with `pip install -e '.[speakers]'`, copied the packaged manual
  example config, and verified doctor, refinement help, model checksums, native
  imports, bundled FFmpeg and packaged review assets.
- Ruff formatting/lint, all 140 offline tests, source distribution and wheel builds
  pass. The existing CI matrix verifies core/offline behavior on Linux and macOS;
  the live optional native backend verification remains macOS Apple Silicon only.

## Not claimed as tested

- No scheduler was registered, no machine reboot was performed, and no unattended
  multi-day collection was observed. launchd syntax is validated, not reboot behavior.
- Optional model inference on Linux or Python 3.11/3.13/3.14 has not been exercised;
  the CI matrix covers the core application and mocked speaker tests.
- Audio diarization does not transcribe new words; it aligns voice labels to existing captions.
- Speaker accuracy has not been scored against a human-labeled reference dataset.

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
