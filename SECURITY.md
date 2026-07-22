# Security policy

## Supported version

The current `0.1.x` line receives security fixes. This is an alpha, supervised robotics application; do not expose the Reachy UI or broker directly to the public internet.

## Reporting a vulnerability

Please use [GitHub private vulnerability reporting](https://github.com/Timverhoogt/reachy-mini-i-spy/security/advisories/new). Do not open a public issue containing credentials, private infrastructure details, captured media, child information, or an exploitable vulnerability.

Include the affected commit or artifact digest, deployment topology, reproduction steps, and whether the issue can affect camera revocation, Stop authority, motion bounds, moderation, speech authorization, scoped credentials, or data retention.

## Deployment expectations

- Keep provider credentials on the Hermes host.
- Use one scoped broker token per Reachy, bound to its device ID.
- Keep Reachy configuration and broker secrets owner-only.
- Bind the broker to loopback and expose only `/ispy/v1/*` through an authenticated TLS proxy.
- Do not enable provider passthrough, arbitrary prompts/models, general agent tools, request logging, or media persistence.
- Treat failures as fail-closed and retain an immediate physical/software Stop path.
