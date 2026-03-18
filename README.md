# HookWall

Universal webhook relay and access-control layer for external event sources.

## Overview

HookWall sits between event-producing clients and destination webhooks so you can relay payloads safely without distributing raw webhook URLs to every sender.

That makes it useful anywhere you want one controlled ingress point for webhook delivery, token-based access, and centralized moderation of who can send events.

Current implementation:

- `webserver/main.py`: FastAPI service that stores tokens and relays authorized webhook requests.
- `bot/main.py`: Discord bot for issuing tokens and managing moderator access.

Today the relay target is a Discord webhook, but the broader role of the project is webhook brokering: receiving events from untrusted or distributed clients and forwarding them through a managed gateway.

## Why HookWall

- Keep destination webhook URLs private.
- Give each sender its own managed token.
- Revoke access without rotating the destination webhook for everyone.
- Centralize relay logic in one service.
- Support plugin, app, automation, or game-event integrations through the same gateway pattern.

## Requirements

- Python 3.11+
- `pip install -r requirements.txt`

## Environment Variables

Create a `.env` file with:

- `DISCORD_BOT_TOKEN` for the management bot
- `ROOT_TOKEN` for privileged API and bot access, unless generated during bootstrap
- `ROOT_ID` for the initial root moderator
- `WEBHOOK_URL` for the current relay destination

Optional:

- `API_BASE_URL` defaults to `http://127.0.0.1:8000` for the bot

## Run

1. Start the API server:

```bash
uvicorn webserver.main:app --reload --host 0.0.0.0 --port 8000
```

Or:

```bash
fastapi dev --reload --host 0.0.0.0 --port 8000 webserver.main:app
```

2. Start the Discord management bot in another terminal:

```bash
python bot/main.py
```

## Behavior

- The webserver persists tokens in `database.db` using SQLite.
- Bearer-token auth controls who can use the relay.
- A root moderator is bootstrapped from `ROOT_ID` and `ROOT_TOKEN`.
- The bot provides token and moderator management commands.
- The `/relay` endpoint forwards authorized event payloads to the configured destination webhook.

## Example Use Cases

- Game plugins sending activity events
- Internal tools that should not expose production webhook URLs
- Lightweight automation clients that need revocable relay credentials
- Shared integrations where many senders feed one destination safely

## Quick Check

- Run `/ping` in Discord to confirm the bot is online.
- Use `/get_token @user` to issue a relay token.
- Send an authorized request to `/relay` and confirm it reaches the configured webhook.

## Notes

- Start the webserver before the bot during local development.
- If you are not using generated bootstrap values, keep `ROOT_TOKEN` and `ROOT_ID` aligned across services.
