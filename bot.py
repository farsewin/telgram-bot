import requests
import json
import time
import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.base import JobLookupError
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ── Config ──────────────────────────────────────────────────────────────────
BOT_TOKEN   = "8862464769:AAFPoALhrKJpKFb2_Li2LM3MEmSwl6wkr1E"
TARGET_URL  = "https://auziatv.com/ruselt.php"
COOKIE_FILE = "cookies.json"
LOG_FILE    = "bot.log"

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ── Scheduler ────────────────────────────────────────────────────────────────
scheduler = BackgroundScheduler()
scheduler.start()

# Store (chat_id → bot) for notifications
_subscribers: dict[int, object] = {}

# ── Cookie helpers ────────────────────────────────────────────────────────────
def load_cookies() -> dict:
    try:
        with open(COOKIE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_cookies(cookies: dict) -> None:
    with open(COOKIE_FILE, "w") as f:
        json.dump(cookies, f, indent=2)

# ── Core request ─────────────────────────────────────────────────────────────
def send_request(chat_id: int | None = None, bot=None) -> None:
    cookies = load_cookies()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "fr-FR,fr;q=0.9",
    }

    try:
        r = requests.get(TARGET_URL, headers=headers, cookies=cookies, timeout=15)
        # Persist any updated cookies the server sends back
        updated = {**cookies, **r.cookies.get_dict()}
        save_cookies(updated)

        msg = f"✅ Request sent — HTTP {r.status_code} ({datetime.now().strftime('%H:%M:%S')})"
        log.info(msg)

        if bot and chat_id:
            import asyncio
            asyncio.run_coroutine_threadsafe(
                bot.send_message(chat_id=chat_id, text=msg),
                bot._loop  # APScheduler runs in a thread; we post to the event loop
            )

    except requests.exceptions.Timeout:
        _notify(bot, chat_id, "⚠️ Request timed out.")
    except Exception as e:
        err = f"❌ Error: {e}"
        log.error(err)
        _notify(bot, chat_id, err)


def _notify(bot, chat_id, text: str) -> None:
    if bot and chat_id:
        import asyncio
        try:
            asyncio.run_coroutine_threadsafe(
                bot.send_message(chat_id=chat_id, text=text),
                bot._loop
            )
        except Exception:
            pass


# ── Telegram handlers ────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *Scheduler Bot*\n\n"
        "Commands:\n"
        "• `/setcookie PHPSESSID=xxx` — save your session cookie\n"
        "• `/showcookie` — show current cookie\n"
        "• `/starttask` — start 24h auto-request\n"
        "• `/stoptask` — stop the scheduler\n"
        "• `/runnow` — trigger a request immediately\n"
        "• `/status` — check scheduler status\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_setcookie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = " ".join(context.args).strip()
    if "PHPSESSID=" not in raw:
        await update.message.reply_text("❌ Format: `/setcookie PHPSESSID=your_value`", parse_mode="Markdown")
        return

    session_id = raw.split("PHPSESSID=", 1)[1].strip()
    if not session_id:
        await update.message.reply_text("❌ Empty session value.")
        return

    cookies = load_cookies()
    cookies["PHPSESSID"] = session_id
    save_cookies(cookies)
    log.info("Cookie updated by user %s", update.effective_user.id)
    await update.message.reply_text("✅ Cookie saved successfully.")


async def cmd_showcookie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cookies = load_cookies()
    if not cookies:
        await update.message.reply_text("No cookie stored yet.")
        return
    session = cookies.get("PHPSESSID", "N/A")
    # Show only last 6 chars for safety
    masked = f"...{session[-6:]}" if len(session) > 6 else session
    await update.message.reply_text(f"🍪 Current PHPSESSID: `{masked}`", parse_mode="Markdown")


async def cmd_starttask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    bot     = context.bot

    # Prevent duplicate jobs
    if scheduler.get_job("auto_request"):
        await update.message.reply_text("⚠️ Task already running. Use /stoptask first.")
        return

    if not load_cookies().get("PHPSESSID"):
        await update.message.reply_text("❌ No cookie set. Use /setcookie first.")
        return

    # Immediate run after 5 s, then every 24 h
    scheduler.add_job(
        send_request,
        "date",
        run_date=datetime.now() + timedelta(seconds=5),
        kwargs={"chat_id": chat_id, "bot": bot},
        id="auto_request_once",
    )
    scheduler.add_job(
        send_request,
        "interval",
        hours=24,
        kwargs={"chat_id": chat_id, "bot": bot},
        id="auto_request",
        next_run_time=datetime.now() + timedelta(hours=24),
    )

    await update.message.reply_text(
        "✅ Task started!\n"
        "• First run in ~5 seconds\n"
        "• Then every 24 hours automatically"
    )


async def cmd_stoptask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    removed = False
    for job_id in ("auto_request", "auto_request_once"):
        try:
            scheduler.remove_job(job_id)
            removed = True
        except JobLookupError:
            pass

    if removed:
        await update.message.reply_text("🛑 Task stopped.")
    else:
        await update.message.reply_text("ℹ️ No active task found.")


async def cmd_runnow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not load_cookies().get("PHPSESSID"):
        await update.message.reply_text("❌ No cookie set. Use /setcookie first.")
        return
    await update.message.reply_text("⏳ Sending request now...")
    chat_id = update.effective_chat.id
    bot     = context.bot
    # Schedule immediately (can't block async handler with sync requests)
    scheduler.add_job(
        send_request,
        "date",
        run_date=datetime.now() + timedelta(seconds=1),
        kwargs={"chat_id": chat_id, "bot": bot},
        id="manual_run",
        replace_existing=True,
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    jobs = scheduler.get_jobs()
    if not jobs:
        await update.message.reply_text("📭 No scheduled jobs running.")
        return

    lines = ["📋 *Active jobs:*"]
    for job in jobs:
        next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S") if job.next_run_time else "N/A"
        lines.append(f"• `{job.id}` — next run: {next_run}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("setcookie",  cmd_setcookie))
    app.add_handler(CommandHandler("showcookie", cmd_showcookie))
    app.add_handler(CommandHandler("starttask",  cmd_starttask))
    app.add_handler(CommandHandler("stoptask",   cmd_stoptask))
    app.add_handler(CommandHandler("runnow",     cmd_runnow))
    app.add_handler(CommandHandler("status",     cmd_status))

    log.info("Bot started.")
    app.run_polling()


if __name__ == "__main__":
    main()
