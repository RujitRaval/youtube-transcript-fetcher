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
