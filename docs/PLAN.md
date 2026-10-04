# Plan — ytube-playlist-bot v2

Status: **approved** (2026-10-04)

## 0. Reality check on the inherited code

| Issue | Impact |
| --- | --- |
| `pytube` unmaintained, broken by YouTube since 2023 | Bot fails on essentially every video |
| python-telegram-bot v13 API (`Updater`, `Filters.text`, `reply_document(f)`) | Removed in PTB v20; the old `bot.py` no longer even imports |
| No `requirements.txt`, no `.gitignore`, token hardcoded as the literal `"BOT_TOKEN"` | Not runnable, not deployable |
| Uploads through the cloud Bot API | Hard-capped at 50 MB, so the 2 GB requirement is unreachable |
| `os.system(f"rm -rf {yid}/")`, `time.sleep()` in the handler path | Fragile, shell injection surface, blocks the event loop |

Verdict: this is a rewrite that keeps the repository and its intent, not a patch.
The surviving part is the product idea, not the code.

## 1. Goals and non-goals

### Goals

- A "glass menu" main menu (inline keyboard): Single video / Playlist / Help / Admin.
- Single-video flow: link -> metadata -> quality menu with real sizes -> 2 GB guard ->
  download -> send -> delete from disk.
- Playlist flow: paginated preview and per-item selection -> quality -> per-item size
  guard -> batch send -> summary report with retry.
- `/cancel`, live progress (percent, transferred bytes, speed, ETA), retry of failures.
- Access control: user-ID allowlist plus a `/request access` flow that admins approve
  with inline buttons.
- SQLite persistence for users, jobs, preferences and access requests, so a restart
  does not lose state.
- Docker for both the bot and a local Telegram Bot API server, with CI/CD to the VPS
  behind the existing Traefik reverse proxy.
- Test-driven development: tests are written before the code they specify.

### Non-goals (v1)

- Webhook mode. Long polling is sufficient and requires no public URL.
- Redis / Celery / horizontal scaling.
- Audio-only downloads, subtitle tracks, or ZIP archives of a playlist.
- Anonymous public access.

## 2. Architecture

Telegram's cloud Bot API limits bot uploads to **50 MB**. Only a self-hosted Bot API
server in `--local` mode allows **2000 MB**. The 2 GB ceiling is therefore that server's
limit, which makes the local server mandatory rather than a nice-to-have.

```
Telegram  ──►  telegram-bot-api  (--local, TELEGRAM_API_ID / TELEGRAM_API_HASH)
                         ▲
                         │  plain HTTP over the compose network, never published
                         ▼
                bot  (PTB v22 + yt-dlp + ffmpeg + SQLite)
                         │
                         ▼
                shared download volume  ──►  removed after every upload
```

Consequences worth stating explicitly:

- **Traefik is not involved.** The Bot API server speaks plain HTTP, but under long
  polling the bot only ever calls it over the internal Docker network
  (`http://telegram-bot-api:8081/bot`). No published port, no certificate, no new
  router. If the project later moves to webhooks, a Traefik router is added then,
  reusing the certificate that is already in place.
- **One-time manual migration.** `deleteWebhook` and `logOut` must be called against
  `api.telegram.org` before the first local run, otherwise the bot stays attached to
  the cloud server. Documented in `docs/RUNBOOK.md`.
- **Upload path.** `local_mode=True` makes `getFile` return an absolute path, so
  download-then-upload of a 2 GB file is avoided. The bot still uploads via multipart
  to the local server, which stays on the same host.

## 3. Technology choices

Versions verified against PyPI / Docker Hub on 2026-10-04.

| Concern | Choice | Rationale |
| --- | --- | --- |
| Language | Python 3.13 (`python:3.13-slim`) | 3.13 is the newest release with full wheel coverage across the dependency set |
| Telegram | `python-telegram-bot` 22.8 | Maintained, async, first-class async test fixtures |
| Downloader | `yt-dlp` 2026.8.19 + system `ffmpeg` | The only maintained option; exposes exact `filesize`, which the size guard depends on |
| Configuration | `pydantic-settings` 2.15 | `SecretStr` plus validation that fails fast on missing required values |
| Database | SQLAlchemy 2.1 (async) + `aiosqlite` + Alembic 1.20 | Standard async stack, migrations included |
| Logging | `structlog` | JSON in production, readable locally, with a token-redacting processor |
| Tests | pytest, pytest-asyncio, pytest-cov | Coverage gate at 85 % |
| Quality | ruff (lint + format), mypy strict | Fast, single binary, standard in the Python ecosystem |
| Bot API server | `aiogram/telegram-bot-api:10.3` | tdlib publishes no official image; this tag is pinned deliberately |

## 4. Feature specification

### Main menu

```
🎬 Single video      📀 Playlist
❓ Help              ⚙️ Admin      (admin only)
```

### Single video

1. Prompt for a link. Accept `youtube.com/watch?v=`, `youtu.be/`, `/shorts/`, `/embed/`.
   Reject playlists and bare IDs early with a specific message.
2. Extract metadata with `yt-dlp` and build quality options: progressive MP4 only
   (`vcodec != none and acodec != none`), grouped by height, best bitrate per height,
   rendered as `720p — 48.2 MB`.
   - Size comes from `filesize`, falling back to `filesize_approx`.
   - Formats with no known size are hidden unless `ALLOW_UNKNOWN_SIZES=true`.
3. Size guard: any option larger than `MAX_UPLOAD_MB` (default 2000) is filtered out,
   and the re-rendered menu states which ones were removed and why.
   - If every option is too large, the user is told and asked for a different video.
4. Download, editing one message in place:
   `⬇ 47% · 22.4/48.2 MB · 3.1 MB/s · ETA 00:08`, throttled to at most one edit per two
   seconds, with a cancel button.
5. Send as video (default, per-user preference) or as document.
6. Delete the file in a `finally` block, so success, failure and cancel all clean up.
   A janitor sweeps orphans older than six hours and enforces a disk-usage ceiling.

### Playlist

1. Parse the playlist ID or URL into a flat listing (no per-video requests).
2. Show `N videos · total duration`, then a paginated preview of eight items per page
   with per-row toggle buttons plus All / None / Invert and navigation.
3. The selection is persisted in SQLite, so a restart mid-selection loses nothing.
4. The quality menu applies to the whole selection, with a per-item size check. When
   some items are too large the user gets Continue / Change quality / Cancel.
5. Batch send with per-item status, then a report of sent / skipped-too-large / failed
   with a Retry failed button.

### Access control

- `ALLOWED_USER_IDS` is enforced by middleware on every update.
- Unknown users get a `/request access` button that creates a row in `access_requests`
  and notifies admins, who approve or deny with inline buttons (or `/approve <id>`).
- Per-user rate limits and a global download concurrency cap keep one user from
  saturating the VPS.

## 5. Repository layout

```
bot/
  __main__.py  app.py  config.py  logging_setup.py  constants.py
  domain/      models.py  errors.py  protocols.py
  services/
    youtube/   service.py  parsing.py  formats.py
    storage/   db.py  orm.py  repositories.py  migrations/
    files.py   users.py  jobs.py
  handlers/    menu.py  single.py  playlist.py  admin.py  errors.py
  keyboards/   reply.py  inline.py
  middleware/  auth.py  ratelimit.py
tests/
  unit/  integration/  contract/  fixtures/
docs/       PLAN.md  CONTRACT.md  ARCHITECTURE.md  RUNBOOK.md
docker/     Dockerfile  entrypoint.sh
.github/    workflows/ci.yml  workflows/cd.yml  dependabot.yml
docker-compose.yml  .env.example  pyproject.toml  Makefile  CHANGELOG.md
```

## 6. Testing strategy

The decision that makes TDD practical: **handlers depend on a `YoutubeService`
Protocol**, never on `yt-dlp` directly. Tests inject a fake, production injects the
adapter.

- **Unit** — URL parsing, quality-option building and de-duplication, the 2 GB guard,
  filename sanitisation, keyboard layouts, configuration validation, repositories.
- **Contract** — the `yt-dlp` adapter is tested against recorded `--dump-single-json`
  fixtures that mirror real YouTube responses, so no network is needed. A
  `@pytest.mark.network` suite hits one real video and is deselected by default.
- **Integration** — complete handler flows driven through PTB's `Application` and
  `Update` fixtures, asserting exact reply texts, button labels and callback payloads.
- **Coverage gate** — `--cov-fail-under=85`; the build fails below it.
- **Pre-commit** — ruff, ruff-format and mypy run on every commit.

## 7. Docker

Multi-stage `Dockerfile` (builder, then slim runtime), non-root `bot` user, `ffmpeg`
installed, read-only root filesystem with tmpfs, healthcheck against `GET /healthz`,
and a named volume shared between the bot and the Bot API server.

```yaml
services:
  telegram-bot-api:   # aiogram/telegram-bot-api:10.3, TELEGRAM_LOCAL=1, no published ports
  bot:                # our image, depends_on telegram-bot-api (healthy)
```

## 8. CI/CD

- `ci.yml` on every pull request and push: ruff, mypy, pytest with coverage, image
  build, Trivy scan, push to GHCR.
- `cd.yml` on push to `main`: build and push to GHCR, SSH to the VPS,
  `docker compose pull && up -d`, poll `/healthz`, and on failure roll back to the
  previous tag and open an issue.
- Dependabot for pip, GitHub Actions and Docker. Conventional Commits, hand-written
  `CHANGELOG.md`.

## 9. Secrets

`.env` is gitignored. `.env.example` is committed with every key documented. Deployment
values (`DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_PATH`, SSH key) exist only in GitHub
Secrets. Tokens are `SecretStr` and are redacted by a structlog processor.

## 10. Delivery phases

| Phase | Content |
| --- | --- |
| 0. Foundations | `.gitignore`, `.env.example`, `pyproject`, package skeleton, config, logging, Dockerfile, compose, Makefile, CI |
| 1. Single video slice | Main menu, link, metadata, quality menu, size guard, download, send, delete |
| 2. Robustness | Progress and ETA, `/cancel`, janitor, disk ceiling, restart recovery, friendly error handler |
| 3. Playlist | Listing, paginated selection, per-item size guard, batch send, report and retry |
| 4. Access control | Allowlist middleware, `/request access`, admin approve/deny, rate limiting |
| 5. Ops and CD | `/healthz`, `/metrics`, GHCR plus SSH deploy with rollback, runbook, README, changelog |

Every phase ends in commits. Conventional Commits, one logical unit per commit,
roughly 15 to 25 commits in total. Nothing is pushed; work stays local.

## 11. Selected enhancements

Agreed additions on top of the base scope:

1. `/metrics` Prometheus endpoint, pairs with the Traefik setup already in place.
2. Restart recovery, so interrupted playlists resume.
3. Per-user preferences (default quality, send-as-video-or-file).
4. Disk quota and automatic cleanup per user.
7. First-run disclaimer and copyright note.
8. `/stats` admin dashboard.

Deferred: internationalisation, `cookies.txt` support for age-restricted videos.
