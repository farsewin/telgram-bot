# Telegram Scheduler Bot

Automates scheduled HTTP requests to your server via a Telegram bot interface.

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Edit `bot.py` and set your bot token:
```python
BOT_TOKEN = "YOUR_TOKEN_HERE"
```

3. Run:
```bash
python bot.py
```

## Commands

| Command | Description |
|---|---|
| `/start` | Show help |
| `/setcookie PHPSESSID=xxx` | Save your session cookie |
| `/showcookie` | Show current cookie (masked) |
| `/starttask` | Start 24h auto-request |
| `/stoptask` | Stop the scheduler |
| `/runnow` | Trigger immediately |
| `/status` | Show active jobs & next run time |

## Files

- `bot.py` — main bot
- `cookies.json` — stored cookie (auto-created)
- `bot.log` — request log (auto-created)
