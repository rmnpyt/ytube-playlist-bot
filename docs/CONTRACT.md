# Contract — ytube-playlist-bot v2

Agreed on 2026-10-04 between the repository owner (rmnpyt) and the implementing agent.
This document is the acceptance criteria for the work described in `docs/PLAN.md`.
Work starts only from this contract; any change to it is negotiated before the code
that depends on it is written.

## 1. Locked decisions

These are decided. Changing one later is a contract amendment, not a preference.

| # | Decision |
| --- | --- |
| D1 | Long polling against a self-hosted Telegram Bot API server (`aiogram/telegram-bot-api:10.3`, `TELEGRAM_LOCAL=1`). No published port, no Traefik router. |
| D2 | `python-telegram-bot` 22.8, async, `local_mode=True`. The v13 API is not used anywhere. |
| D3 | `yt-dlp` is the only download engine. `pytube` is removed. |
| D4 | Progressive MP4 formats only (`vcodec != none and acodec != none`) are offered as quality choices. |
| D5 | The upload ceiling is `MAX_UPLOAD_MB`, default 2000, matching the local server's documented 2000 MB limit. Options above the ceiling are removed from the menu rather than offered and rejected. |
| D6 | Handlers depend on the `YoutubeService` Protocol. No handler imports `yt-dlp`. |
| D7 | Python 3.13, SQLAlchemy 2.1 async + `aiosqlite`, Alembic for migrations, pydantic-settings for configuration. |
| D8 | Every configuration value comes from the environment, documented in a committed `.env.example`. No secret is ever committed. |
| D9 | Coverage gate is 85 %. CI fails below it. |
| D10 | Work happens on the local `main` branch. Nothing is ever pushed without an explicit instruction. |
| D11 | Commits follow Conventional Commits and are small enough that each one is independently reviewable. |
| D12 | The downloaded file is deleted in a `finally` block, on every path: success, failure, cancel. |

## 2. Invariants

The bot must never:

1. Upload a file larger than `MAX_UPLOAD_MB`.
2. Leave a downloaded file on disk after a job ends, for any reason.
3. Accept work from a Telegram user outside `ALLOWED_USER_IDS`.
4. Write a secret to stdout, stderr, or the database.
5. Block the event loop during a download. All network and disk work is offloaded.
6. Invoke a shell. The old `os.system("rm -rf ...")` pattern is gone for good.
7. Run as root inside the container.

## 3. Acceptance criteria

### Phase 0 — Foundations

- `ruff check`, `ruff format --check` and `mypy` all pass on a clean checkout.
- `pytest` passes with coverage at or above 85 %.
- `docker compose config` validates, and both images build.
- A missing required variable (for example `TELEGRAM_BOT_TOKEN`) fails startup with a
  message naming the variable, not a stack trace.
- Secrets are redacted from log output; a test proves it.

### Phase 1 — Single video

Given a user with access and a valid single-video URL:

- `/start` renders the main menu with the four expected buttons.
- Choosing "Single video" prompts for a link.
- An invalid link produces a specific, friendly error and the flow stays usable.
- A valid link produces a quality menu where every button shows resolution and size.
- A quality whose size exceeds `MAX_UPLOAD_MB` is absent from the menu, and the menu
  states which options were removed and why.
- If every option exceeds the ceiling, the bot explains the situation and stops.
- After choosing a valid quality the file downloads, progress is reported in place,
  the video is sent, and the file is gone from disk afterwards.
- Cancelling mid-download removes the partial file.
- Each of these paths has an automated test that fails if the behaviour regresses.

### Phase 2 — Robustness

- Progress messages are throttled, never flooding the chat.
- A restart mid-playlist leaves no orphaned files and no stuck jobs.
- The janitor removes orphaned files older than the configured age.
- Disk usage above the configured ceiling stops new jobs with a clear message instead
  of filling the VPS.

### Phase 3 — Playlist

- Playlist listing, pagination and per-item toggles work and are covered by tests.
- A selection survives a bot restart.
- Items above the ceiling are skipped with an explicit count, or block on confirmation.
- The final report states sent, skipped and failed counts, and retry works.
- Per-user concurrency and rate limits are enforced and tested.

### Phase 4 — Access control

- A user outside the allowlist receives a "request access" message and nothing else.
- The request reaches the admins with working approve and deny buttons.
- Approving adds the user to the allowlist, persisted in SQLite.
- Denying removes the pending request.

### Phase 5 — Operations and CD

- `/healthz` and `/metrics` respond on the metrics port.
- CI runs lint, typecheck and tests on every push and pull request.
- CD builds and pushes to GHCR, deploys over SSH, health-checks, and rolls back to the
  previous tag if the health check fails.
- `docs/RUNBOOK.md` covers first-time setup, the `logOut` migration, deploy, rollback,
  log inspection and troubleshooting.

## 4. Out of scope for v1

Webhook mode, Redis or Celery, horizontal scaling, audio-only downloads, subtitle
selection, ZIP archives, playlists larger than `MAX_PLAYLIST_ITEMS`, internationalisation
and `cookies.txt` support for age-restricted videos. Any of these can be added later
through a contract amendment.

## 5. Working agreement

- Commits are made locally in logical units as each phase progresses. The user reviews
  the history.
- **No push, no force-push, no tag publication, no remote mutation** without an explicit
  instruction for that specific operation.
- The user's existing Git identity is used. No AI attribution is added to commits.
- `user.name` and `user.email` are not modified.
- TDD means the test exists and fails before the implementation exists and passes after.

## 6. Inputs the user supplies

| Item | Status |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | supplied via local `.env`, never committed |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | available |
| Admin Telegram user ID | to be filled in `.env` |
| Allowlisted user IDs | to be filled in `.env` |
| VPS host, SSH user, deploy path, SSH key | required before Phase 5 CD wiring |

## 7. Change process

To change scope, the owner states the change, the contract section and phase it affects
are updated in this file, and only then does the corresponding work begin.
