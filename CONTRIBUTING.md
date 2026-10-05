# Contributing

Use Python 3.11+ on Linux or macOS. Keep this a local command-line utility.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
ruff format .
ruff check .
pytest
python -m build
```

Tests block network connections. Mock provider boundaries and include regression
coverage for state transitions, storage recovery, and retries when changing them.
Do not add a model, daemon, web server, telemetry, cookies, or paid API to the normal
workflow. Do not commit personal configs, databases, transcripts, signed caption
URLs, or logs. See docs/architecture.md for design decisions.

Open a focused issue/PR describing the problem, final behavior, and test results.
External YouTube changes should be reproduced separately from deterministic tests;
include Python/library versions and redacted error kinds, never authentication data.

Speaker tests use small PCM fixtures and mocked inference, so `.[dev]` is enough
for the offline suite. For manual model verification, install `.[dev,speakers]`,
run `speakers setup`, and follow the README single-video workflow. Use an isolated
config/output/database when testing refinement so personal names and corrections
are not replaced by a fresh analysis. Audio, models and private review data stay
out of Git. New CLI behavior should have offline failure/preservation tests.
