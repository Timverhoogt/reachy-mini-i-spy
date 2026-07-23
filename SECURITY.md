# Security policy

## Supported version

The historical `0.1.0` artifact is delisted. Security work targets the in-development standalone provider release. This remains an alpha, supervised robotics application; do not expose its local UI directly to the public internet.

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/Timverhoogt/reachy-mini-i-spy/security/advisories/new). Do not open a public issue containing credentials, private infrastructure details, captured media, child information or an exploitable vulnerability.

Include the affected commit or artifact digest, deployment profile (Lite host or Wireless), reproduction steps and whether the issue can affect camera revocation, Stop authority, motion bounds, moderation, speech authorization, credentials or data retention.

## Deployment expectations

- Enter provider credentials only through the local caregiver UI on the machine running the Reachy daemon.
- Local mode needs no provider credential; install voices only through the built-in pinned/checksummed setup action.
- Keep `~/.config/reachy-mini-i-spy/config.json` owner-only (`0600`) and its parent directory `0700`.
- Do not put credentials in source, wheel files, service arguments, logs, screenshots or support reports.
- Do not expose caller-controlled provider URLs, models, prompts, tools or voices.
- Do not enable request-body logging or media persistence.
- On Lite, secure the connected Mac/PC account; on Wireless, secure Reachy's local account and network access.
- Treat failures as fail-closed and retain an immediate physical/software Stop path.
