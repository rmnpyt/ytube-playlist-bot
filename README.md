# ytube-playlist-bot

A Telegram bot that downloads YouTube videos and playlists and delivers them as
files. Runs against a **local Telegram Bot API server** so it can deliver files
well past the cloud API's 50 MB limit.

---

## Status: v2 rewrite in progress

The bot was rewritten from scratch because the original could not be made to work
reliably: it targeted `python-telegram-bot` v13, which is long unmaintained, and
depended on `pytube`, which no longer downloads from YouTube.

What exists on this branch today:

| Area | State |
| --- | --- |
| Project scaffolding, linting, types, tests | done |
| Validated configuration from the environment | done |
| Structured logging with secret redaction | done |
| Container images and Compose stack | done, builds and validates |
| Continuous integration | done |
| Telegram handlers, database, downloads, uploads | **not started** |

The bot currently starts, validates its configuration and exits. It does not
answer messages yet. See [`docs/PLAN.md`](docs/PLAN.md) for the phase breakdown and
[`docs/CONTRACT.md`](docs/CONTRACT.md) for the decisions behind it.

---

## Why a second container?

Telegram's cloud API will not accept a bot upload larger than **50 MB**. A single
YouTube video above that size is common, so a bot that only talks to the cloud API
cannot do the job it exists for.

The local Bot API server, running in `--local` mode, raises the limit to
**2000 MB**. That is the only reason the `telegram-bot-api` service exists.

Under long polling the bot reaches it over the internal Compose network, so it is
never published to the host. If the local server is unavailable, the bot falls back
to the cloud API and automatically hides quality options above 50 MB.

---

## Running it

```bash
cp .env.example .env      # fill in the four required values
docker compose up -d
docker compose logs -f bot
```

The four values you must supply:

| Variable | Where to get it |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_API_ID` | [my.telegram.org](https://my.telegram.org) → API development tools |
| `TELEGRAM_API_HASH` | my.telegram.org → API development tools |
| `ADMIN_USER_IDS` | your own Telegram user id |

Everything else has a working default and is documented, with its reasoning, in
[`.env.example`](.env.example).

Health and metrics are served on `127.0.0.1:9090` inside the VPS. That is reachable
from the host, not from the internet; put a Traefik router in front of it if you want
a dashboard.

---

## Development

```bash
make install        # virtual environment plus dev dependencies
make check          # format, lint, types, tests: what CI runs
make coverage       # the same, with an HTML report
make test           # just the suite
```

On Windows, point `make` at the virtual environment:

```bash
make PYTHON=.venv/Scripts/python.exe check
```

Tests, linting and type checking run without Docker. Only `make up` and `make build`
need it.

---

## Documentation

| Document | Purpose |
| --- | --- |
| [`docs/PLAN.md`](docs/PLAN.md) | Scope, phases, architecture, testing strategy |
| [`docs/CONTRACT.md`](docs/CONTRACT.md) | Binding decisions and acceptance criteria |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Module map and the reasoning behind it |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | Day-to-day operation and what to do when it breaks |
| [`CHANGELOG.md`](CHANGELOG.md) | Release history |

---

## License

[MIT](LICENSE). That covers redistribution of this code only; it does not address
YouTube's Terms of Service or the rights to the videos a user downloads, which are
separate questions.

