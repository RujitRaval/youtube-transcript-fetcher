# Security and privacy

This application stores transcripts and state locally. It has no analytics,
telemetry, automatic uploads, required secrets, or cookie/browser-profile access.
YouTube requests necessarily disclose your IP address and requested videos to YouTube.
Only HTTPS YouTube channel/video inputs are accepted. Captions and titles are treated
as data; filenames are sanitized and metadata is encoded as JSON. No transcript is
executed or sent to a model. Native scheduler examples use a private umask.

Keep the config, SQLite database, output folder and scheduler files writable only by
trusted local users. Use a local disk; network filesystems may not honor file locks
or SQLite/rename semantics reliably. Dependencies execute code locally: install from
trusted package sources, review upgrades, and keep Python and dependencies current.

Report suspected vulnerabilities privately through the repository's GitHub Security
Advisories (Security → Report a vulnerability), when enabled by its maintainer. If
private reporting is unavailable, open an issue requesting a private contact without
exploit details or private data. This unreleased project has no dedicated security
response team. Only the newest release is supported.
