# Runbook

Operating the bot on the VPS. Everything here assumes the repository is checked out at
`/opt/ytube-playlist-bot` and `.env` is filled in.

> The bot does not answer messages yet: see the status table in the
> [README](../README.md). This runbook describes the deployment as it is being built
> and will gain real procedures as each phase lands.

## Daily commands

```bash
cd /opt/ytube-playlist-bot

make up          # build and start
make logs        # follow the bot
make restart     # restart the bot only
make ps          # what is running
make down        # stop, keep data
```

`make up` rebuilds the image, so `git pull && make up` is the whole upgrade procedure.

## Verifying a deployment

```bash
docker compose ps                            # both services running
docker compose logs --tail=50 bot            # bot.startup event, no errors
curl -s localhost:9090/healthz               # {"status":"ok"} once implemented
curl -s localhost:9090/metrics | head        # Prometheus text format
docker compose exec bot env | sort            # confirm the settings actually loaded
```

A healthy startup log contains one `bot.startup` event with the API URL, the mode and
the limits in force. If it contains `ConfigurationError` instead, the process exited
with code 2 and the message names the variables at fault.

## Logs

Structured JSON, one object per line, rotated by the Docker logging driver at 10 MB
across 5 files. Useful queries:

```bash
# Everything that went wrong since the last restart
docker compose logs bot | grep '"level": "error"'

# One user's activity
docker compose logs bot | grep '"user_id": 12345'
```

Tokens and API hashes never appear in the output, by construction.

## Backups

Two pieces of state, both in named volumes:

| Volume | Contents | Back it up? |
| --- | --- | --- |
| `bot-data` | SQLite database and temporary downloads | yes |
| `bot-api-data` | Bot API server state, including the bot session | no |

```bash
docker run --rm -v ytube-playlist-bot_bot-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/bot-data-$(date +%F).tgz -C /data .
```

> Never delete `bot-api-data` while the bot is running. Doing so makes Telegram forget
> the local server and every download fails until it re-registers.

## What to do when things break

### The bot will not start

Read the last lines of `docker compose logs bot`. Configuration problems are named
explicitly:

```bash
docker compose logs bot | grep 'Field required'
```

### Downloads fail but the bot is up

Almost always the Bot API server. Check it first:

```bash
docker compose ps telegram-bot-api
docker compose logs --tail=50 telegram-bot-api
```

If the local server is unhealthy the bot should have fallen back to the cloud API, in
which case quality options above 50 MB are gone. That fallback is expected behaviour,
not a second fault.

### A download filled the disk

The bot refuses new downloads below `DISK_FREE_THRESHOLD_GB`, but an in-flight
download can still cross the line. Clean up and raise the threshold:

```bash
df -h /var/lib/docker
docker compose exec bot ls -lh /data/downloads
docker compose exec bot rm -rf /data/downloads/*
docker compose restart bot
```

Partial files older than `ORPHAN_TTL_SECONDS` are removed by the janitor once it
exists. Until then, the command above is the manual version of it.

### Everything is broken after an upgrade

Roll back to the previous commit and start again:

```bash
git log --oneline -10
git checkout <previous-sha>
make up
```

The database is migrated forward by Alembic; going backwards may require a restore
from backup. Take one before any upgrade that follows a schema change.

## Configuration changes

Edit `.env`, then:

```bash
docker compose up -d          # picks up the new values
```

Not every value can change without a restart. `LOG_LEVEL` and `LOG_JSON` are read at
startup. Restart the bot after editing either:

```bash
docker compose restart bot
```

## Monitoring

`/metrics` on `127.0.0.1:9090` exposes the Prometheus text format, and `/healthz`
returns 200 while the process is serving. Both are bound to loopback on purpose:
put a Traefik router in front of the port if you want them on a dashboard, and
terminate TLS there as you already do for everything else.

Worth alerting on:

| Signal | Means |
| --- | --- |
| `bot_health_up` | the process is serving |
| download failure rate rising | usually YouTube or the network |
| free disk below the threshold | downloads are about to be refused |
| container restart count climbing | crash loop; read the logs |
