# Security and privacy

This application stores transcripts and state locally. It has no analytics,
telemetry, automatic uploads, required secrets, or cookie/browser-profile access.
YouTube requests necessarily disclose your IP address and requested videos to YouTube.
Only HTTPS YouTube channel/video inputs are accepted. Captions and titles are treated
as data; filenames are sanitized and metadata is encoded as JSON. No transcript is
executed or sent to a cloud model. Native scheduler examples use a private umask.

Optional speaker analysis explicitly downloads audio (unless a local file is
supplied) and runs local models. Model setup contacts GitHub release hosting;
pinned hashes are verified before installation. Audio and original model output
stay on disk alongside the transcript. The review server binds to `127.0.0.1`,
checks the Host header, and requires a same-origin token for edits. It is intended
for one trusted local user, not a public or shared-network deployment. Audit
history preserves normal application edits but is not tamper-proof against
someone with filesystem access.

Keep the config, SQLite database, output folder and scheduler files writable only by
trusted local users. Use a local disk; network filesystems may not honor file locks
or SQLite/rename semantics reliably. Dependencies execute code locally: install from
trusted package sources, review upgrades, and keep Python and dependencies current.

Report suspected vulnerabilities privately through the repository's GitHub Security
Advisories (Security → Report a vulnerability), when enabled by its maintainer. If
private reporting is unavailable, open an issue requesting a private contact without
exploit details or private data. This project has no dedicated security
response team. Only the newest release is supported.
