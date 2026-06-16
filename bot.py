import requests
import json
import time
import logging
import sqlite3
import threading
import os
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ── Config ───────────────────────────────────────────────────────────────────
BOT_TOKEN   = "8461040224:AAFgd-njpXXTpzuVHD39_sKr1yVRC6sv13c"
TARGET_URL  = "https://auziatv.com/ruselt.php"
COOKIE_FILE = "cookies.json"
DB_FILE     = "bot.db"
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

# ── DB (persistent state) ────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS state (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.close()

def set_state(key, value):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT OR REPLACE INTO state(key,value) VALUES(?,?)", (key, str(value)))
    conn.commit()
    conn.close()

def get_state(key):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT value FROM state WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

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

# ── Core request ──────────────────────────────────────────────────────────────
def send_request(app=None, chat_id=None) -> None:
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
        updated = {**cookies, **r.cookies.get_dict()}
        save_cookies(updated)

        msg = f"✅ Request sent — HTTP {r.status_code} ({datetime.now().strftime('%H:%M:%S')})"
        log.info(msg)
        set_state("last_run", datetime.utcnow().isoformat())

        if app and chat_id:
            _notify(app, chat_id, msg)

    except requests.exceptions.Timeout:
        log.warning("Request timed out.")
        if app and chat_id:
            _notify(app, chat_id, "⚠️ Request timed out.")
    except Exception as e:
        err = f"❌ Error: {e}"
        log.error(err)
        if app and chat_id:
            _notify(app, chat_id, err)

def _notify(app, chat_id, text: str) -> None:
    try:
        import asyncio
        asyncio.run_coroutine_threadsafe(
            app.bot.send_message(chat_id=chat_id, text=text),
            app.loop
        )
    except Exception as ex:
        log.warning("Notify failed: %s", ex)

# ── Background worker (replaces APScheduler) ─────────────────────────────────
_app_ref = None  # set in main()

def worker():
    while True:
        try:
            if get_state("enabled") == "1":
                chat_id  = get_state("chat_id")
                last_run = get_state("last_run")

                should_run = False
                if not last_run:
                    should_run = True
                else:
                    last = datetime.fromisoformat(last_run)
                    if datetime.utcnow() - last >= timedelta(hours=24):
                        should_run = True

                if should_run:
                    send_request(app=_app_ref, chat_id=int(chat_id) if chat_id else None)

        except Exception as e:
            log.error("Worker error: %s", e)

        time.sleep(60)

# ── Health server (keeps Render alive) ───────────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args):
        pass  # silence HTTP logs

def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    log.info("Health server on port %d", port)
    server.serve_forever()

# ── Telegram handlers ─────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Scheduler Bot\n\n"
        "Commands:\n"
        "• /setcookie PHPSESSID=xxx — save your session cookie\n"
        "• /showcookie — show current cookie\n"
        "• /starttask — start 24h auto-request\n"
        "• /stoptask — stop the scheduler\n"
        "• /runnow — trigger a request immediately\n"
        "• /status — check scheduler status"
    )

async def cmd_setcookie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = " ".join(context.args).strip()
    if "PHPSESSID=" not in raw:
        await update.message.reply_text("❌ Format: /setcookie PHPSESSID=your_value")
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
    masked = f"...{session[-6:]}" if len(session) > 6 else session
    await update.message.reply_text(f"🍪 Current PHPSESSID: {masked}")

async def cmd_starttask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not load_cookies().get("PHPSESSID"):
        await update.message.reply_text("❌ No cookie set. Use /setcookie first.")
        return
    chat_id = update.effective_chat.id
    set_state("enabled", "1")
    set_state("chat_id", chat_id)
    set_state("last_run", "")  # force immediate run on next worker tick
    await update.message.reply_text(
        "✅ Task started!\n"
        "• First run within 1 minute\n"
        "• Then every 24 hours automatically"
    )

async def cmd_stoptask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_state("enabled", "0")
    await update.message.reply_text("🛑 Task stopped.")

async def cmd_runnow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not load_cookies().get("PHPSESSID"):
        await update.message.reply_text("❌ No cookie set. Use /setcookie first.")
        return
    await update.message.reply_text("⏳ Sending request now...")
    chat_id = update.effective_chat.id
    threading.Thread(
        target=send_request,
        kwargs={"app": context.application, "chat_id": chat_id},
        daemon=True
    ).start()

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    enabled  = get_state("enabled")
    last_run = get_state("last_run")
    chat_id  = get_state("chat_id")

    status = "🟢 Running" if enabled == "1" else "🔴 Stopped"
    last   = last_run if last_run else "Never"
    await update.message.reply_text(
        f"Status: {status}\n"
        f"Last run: {last}\n"
        f"Subscribed chat: {chat_id or 'None'}"
    )

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    global _app_ref

    init_db()

    # Health server thread
    threading.Thread(target=start_health_server, daemon=True).start()

    # Background 24h worker thread
    threading.Thread(target=worker, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()
    _app_ref = app

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("setcookie",   cmd_setcookie))
    app.add_handler(CommandHandler("showcookie",  cmd_showcookie))
    app.add_handler(CommandHandler("starttask",   cmd_starttask))
    app.add_handler(CommandHandler("stoptask",    cmd_stoptask))
    app.add_handler(CommandHandler("runnow",      cmd_runnow))
    app.add_handler(CommandHandler("status",      cmd_status))

    log.info("Bot started.")
    app.run_polling()

if __name__ == "__main__":
    main()