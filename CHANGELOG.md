# Changelog

All notable changes to this project are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

The v2 rewrite. Nothing in this section has been released: the bot does not yet
answer messages.

### Added

- Validated configuration loaded from the environment, with a documented
  `[.env.example](.env.example)` covering every setting, its unit and its limits.
- Structured JSON logging with a console renderer for local work, and secret
  redaction that masks sensitive keys and known secret values wherever they
  appear, including inside log messages.
- Process entrypoint that fails fast with the names of any missing settings
  instead of starting in a broken state.
- Two-stage container image for the bot, running unprivileged behind `tini` with
  a read-only root filesystem.
- Compose stack pairing the bot with a local Telegram Bot API server, reachable
  only over the internal network.
- Continuous integration covering formatting, linting, type checking, the test
  suite and the container build, plus Dependabot for Python, Actions and Docker.
- Developer entry points in a `Makefile`.
- Janitor that removes downloads left behind by a hard kill, on
  `JANITOR_INTERVAL_SECONDS` and once before the bot starts serving, so a
  container restart cannot leak files until the disk fills.
- Disk guard that refuses to start a download when free space drops below
  `DISK_FREE_THRESHOLD_GB`, checked before any transfer begins so a full disk
  cannot leave a half-written file or a stale cancel button.

### Changed

- The bot no longer targets `python-telegram-bot` v13. It targets 22.8 on Python
  3.13.
- The single-file `bot.py` is gone, replaced by a `bot` package.

### Removed

- The `pytube` downloader, which no longer downloads from YouTube. Downloads go
  through `yt-dlp`.

### Notes

- The project is released under the MIT licence.
