# OVHcloud VPS Telegram Checker

A lightweight Python Telegram bot that monitors OVHcloud VPS availability across European datacenters.

The bot uses OVHcloud's public VPS order availability endpoint, filters datacenters whose region code starts with `eu-`, and checks the selected OS availability. No OVHcloud API credentials are required.

## Features

- Choose the OS and VPS type from Telegram inline buttons
- Check availability on demand with `/check`
- Monitor continuously with `/watch`
- Notify only when stock appears
- Optional automatic monitoring on startup with `TELEGRAM_CHAT_ID`
- Docker-ready and configurable through environment variables

## Requirements

- Python 3.11+ for local usage
- A Telegram bot token from `@BotFather`
- Internet access to Telegram and `eu.api.ovh.com`

## Configuration

Copy the example environment file:

```bash
cp .env.example .env
```

Set at least:

```env
TELEGRAM_BOT_TOKEN=123456:replace_me
```

Available settings:

| Variable | Default | Description |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | required | Telegram bot token from `@BotFather`. |
| `TELEGRAM_CHAT_ID` | empty | Optional chat ID to start monitoring automatically when the bot starts. Use `/id` to retrieve it. |
| `OVH_PLAN_CODE` | `vps-2025-model1` | Default OVHcloud plan code. Supported values are `vps-2025-model1` through `vps-2025-model6`. |
| `OVH_OS` | `linux` | Default OS to monitor. Supported values are `linux` and `windows`. |
| `OVH_SUBSIDIARY` | `FR` | OVHcloud subsidiary used by the public availability endpoint. |
| `CHECK_INTERVAL_SECONDS` | `60` | Monitoring interval in seconds. |

## Local Run

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Start the bot:

```bash
python bot.py
```

## Docker Run

Build the image:

```bash
docker build -t ovh-vps-checker-bot .
```

Run with an `.env` file:

```bash
docker run --rm --env-file .env ovh-vps-checker-bot
```

Or pass variables directly:

```bash
docker run --rm \
  -e TELEGRAM_BOT_TOKEN=123456:replace_me \
  -e CHECK_INTERVAL_SECONDS=60 \
  ovh-vps-checker-bot
```

## Telegram Commands

| Command | Description |
| --- | --- |
| `/start` | Show the OS and VPS type selector. |
| `/settings` | Show the OS and VPS type selector again. |
| `/check` | Check availability now using the current chat settings. |
| `/watch` | Start monitoring the current chat. |
| `/stop` | Stop monitoring the current chat. |
| `/id` | Print the current Telegram chat ID. |

## Selectable VPS Types

| Telegram label | OVHcloud plan code |
| --- | --- |
| `VPS-1` | `vps-2025-model1` |
| `VPS-2` | `vps-2025-model2` |
| `VPS-3` | `vps-2025-model3` |

## How It Works

The bot calls:

```text
https://eu.api.ovh.com/v1/vps/order/rule/datacenter
```

with:

```text
ovhSubsidiary=FR
planCode=vps-2025-model1
```

It keeps only European datacenters, identified by OVHcloud region codes beginning with `eu-`, then reports either `linuxStatus` or `windowsStatus` depending on the selected OS.

## Notes

- The bot does not order a VPS. It only reports availability.
- During continuous monitoring, it sends a notification when availability changes from unavailable to available for the selected OS and VPS type.
- OVHcloud stock can disappear quickly, so a low interval such as `30` or `60` seconds is practical.
