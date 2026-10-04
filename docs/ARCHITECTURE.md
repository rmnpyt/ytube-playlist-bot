# Architecture

This document explains how the bot is put together and, more importantly, why. The
decisions themselves are recorded in [`CONTRACT.md`](CONTRACT.md); this is the shape
they take in code.

## The shape of the system

Two processes, one network, no ingress.

```
                      ┌───────────────────────────┐
   Telegram ─────────▶│  telegram-bot-api (10.3)  │   cloud API fallback
                      │  --local, 2000 MB uploads │
                      └─────────────┬─────────────┘
                                    │ bot HTTP API, internal network only
                      ┌─────────────▼─────────────┐
                      │         the bot           │   ptb polling + handlers
                      │  python -m bot            │
                      └──────┬───────────┬────────┘
                             │           │
                  ┌──────────▼───┐   ┌───▼──────────┐
                  │   yt-dlp     │   │  SQLite      │
                  │   ffmpeg     │   │  aiosqlite   │
                  └──────────────┘   └──────────────┘

                  127.0.0.1:9090 ──▶ /healthz, /metrics
```

Nothing but the Bot API needs to be reachable from outside. The bot is long-polling,
so it opens the connection and there is no webhook, no certificate and no public
port for the bot itself.

## Why the local Bot API server is not optional

The cloud Bot API rejects uploads above 50 MB. Quality options above that size
therefore have to disappear whenever the bot is talking to the cloud. That single
constraint drives several decisions:

- The `telegram-bot-api` service exists and is pinned to an image tag.
- `MAX_UPLOAD_MB` is capped at 2000 and validated at startup, because that is the
  local server's limit.
- `TELEGRAM_BOT_API_LOCAL` exists, because local mode changes how `getFile` returns
  paths: local mode returns an absolute filesystem path, which is the only reason a
  2 GB file does not have to be copied across HTTP.
- The health check prefers local mode and degrades to the cloud rather than
  declaring itself unhealthy.

## Module map

| Package | Responsibility |
| --- | --- |
| `bot.__main__` | Entry point. Validates configuration, configures logging, reports readiness. |
| `bot.config` | `Settings`: the whole environment surface, validated once at startup. |
| `bot.logging_setup` | `structlog` configuration and the secret redaction processor. |
| `bot.domain` | Value objects shared by everything else. No I/O, no framework types. |
| `bot.handlers` | One module per user-facing command, returning nothing but thin calls into services. |
| `bot.keyboards` | Inline keyboards. Layout only, no behaviour. |
| `bot.middleware` | Access control, per-user rate limiting, metrics. |
| `bot.services.storage` | The database: models, repositories, migrations. |
| `bot.services.youtube` | `yt-dlp` and `ffmpeg`. The only code that spawns processes. |

The dependency direction is one way only: `handlers` and `middleware` may use
`services`, `services` may use `domain` and `config`, and `domain` uses nothing.
Nothing below `services` knows that Telegram exists, which is what makes the download
and storage logic testable without a bot instance.

## Design decisions worth knowing

### Configuration is validated once, at the edge

`Settings.from_env()` runs exactly once, in `__main__`, and a failure is fatal. No
handler reads `os.environ`. That means an invalid value is a startup failure with a
readable message, not a `KeyError` in the middle of a user's download.

Paths are validated too: `DOWNLOAD_DIR` must be absolute and must already exist. A
container that cannot write where it was told to write should say so on boot.

### Redaction lives in one processor, not in call sites

The tempting design is a helper that every call site remembers to use. It fails
eventually, at 3am, in the log line nobody read. Instead, `redact_secrets` is a
`structlog` processor, so it applies to every event without any call site knowing it
exists. It masks two ways at once, because either alone has a gap: keys whose *name*
looks sensitive, and known secret *values* anywhere they appear, including inside an
otherwise ordinary message.

Secrets are `pydantic.SecretStr`, so printing a `Settings` object by accident does not
print a token.

### One renderer decision, made once

`LOG_JSON` chooses between JSON and a colourised console at startup, and both route
through stdlib logging. That is what brings `python-telegram-bot` and SQLAlchemy
output into the same stream with the same shape, rather than leaving third-party
lines interleaved in a different format.

### Nothing logs secrets, even by accident

The startup event records the API URL, the mode, the limits and the counts of
configured users. It does not record the token or hash, not even masked. Redaction is
the second line of defence, not the first.

### Downloads are the only subprocess boundary

`bot.services.youtube` is the only module that spawns `yt-dlp` and `ffmpeg`. Everything
else deals in files and paths. This is why the container runs under `tini`: it exists to
reap the subprocesses this one module creates.

## Data flow for a single video

1. A handler validates access and rate limits, then calls a service.
2. The service asks the repository for the format list, which already excludes options
   over `MAX_UPLOAD_MB`.
3. The user picks a format. The download runs in `yt-dlp`, streamed to `DOWNLOAD_DIR`.
4. On success the file is uploaded through the Bot API client, then deleted.
5. On any failure the file is left for the janitor, which deletes anything older than
   `ORPHAN_TTL_SECONDS`.

Every step is recorded against a job row, so a restart resumes rather than restarts.
