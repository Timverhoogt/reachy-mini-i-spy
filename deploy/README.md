# Hermes-host I Spy broker deployment

The companion in `hermes_broker/` runs only on the Hermes host. It is not part of the `reachy_mini_i_spy` wheel. It offers exactly six POST routes under `/ispy/v1`: four provider decisions, TTS, and session cancellation. There is no provider passthrough, model selection, arbitrary prompt, agent, tool, memory, or OpenAI-compatible endpoint.

## 1. Install on Hermes

Create a dedicated checkout and virtual environment under `/opt/hermes-ispy-broker`, then install the broker extra:

```sh
cd /opt/hermes-ispy-broker
uv venv
uv pip install --python .venv/bin/python '.[broker]'
```

Do not build the secrets file into an image, wheel, archive, or service argument. Do not put a key in a shell command, `.env`, repository file, Reachy config, or reverse-proxy configuration.

## 2. Create owner-only host secrets

Generate a separate random client token for each Reachy (`python -c 'import secrets; print(secrets.token_urlsafe(32))'`). Using a root-only editor that does not create world-readable backups, create `/etc/hermes-ispy-broker/secrets.json` with this shape:

```json
{
  "openai_api_key": "REPLACE_INTERACTIVELY_ON_HERMES",
  "clients": {
    "REPLACE_WITH_RANDOM_SCOPED_TOKEN": "reachy-one"
  }
}
```

Then run `chown root:root /etc/hermes-ispy-broker/secrets.json` and `chmod 600 /etc/hermes-ispy-broker/secrets.json`. The broker refuses group/world-readable files and unknown keys. The scoped token authorizes only this broker and is cryptographically bound to the listed device ID; it is not an OpenAI key or Hermes agent/tool token.

## 3. Install and expose safely

Review and install `deploy/hermes-ispy-broker.service`, then enable it with systemd. The unit binds only to `127.0.0.1:8065`, disables access logs and proxy-header trust, passes secrets through systemd credentials, and keeps credentials out of process arguments.

Terminate TLS at the existing authenticated Hermes reverse proxy and map only `/ispy/v1/*` to this loopback service. Do not proxy `/`, arbitrary upstream paths, WebSockets, redirects, or an OpenAI-compatible prefix. Apply an outer request-body cap of 5 MB and a timeout no greater than 20 seconds. The application independently enforces strict JSON, bounded frames/text/output, fixed models/prompts, no redirects, per-device/session sequencing, cancellation/late-result rejection, and per-device rate limits.

Configure Reachy with only:

- broker URL ending in `/ispy/v1`;
- the scoped per-device token;
- its matching device ID.

The Reachy config is owner-only and its status API reports only whether the scoped token exists. Provider keys, model controls, prompts, and provider URLs are neither accepted nor disclosed.

## 4. Verification before deployment

Run the repository checks documented in the main README. Also inspect the built wheel and source archive: neither `hermes_broker/`, `deploy/`, local config, `.env`, nor credentials may be present in the Reachy wheel. Search artifacts for known secret canaries before transfer. Keep provider HTTP/access logging disabled; broker warnings intentionally include only exception types, never request bodies, guesses, frames, tokens, provider responses, or keys.
