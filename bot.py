import aiohttp
import asyncio
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import RetryAfter

# === CONFIG ===
TOKEN = '7981403358:AAGON5Hycw_2UlKfO_mPa_99p7OTHqwnauo'  # Replace with your bot token
API_URL = "https://learn.aakashitutor.com/api/getquizfromid?nid="
BATCH_SIZE = 500

# === GLOBAL STATE ===
scanning_task = None
authorized_users = {7796598050}
scan_status = {
    "active": False,
    "start": None,
    "end": None,
    "current": None,
    "scanned": 0,
    "found": 0
}

# === LOGGING ===
logging.basicConfig(format="%(asctime)s - %(message)s", level=logging.INFO)

# === SAFE SEND WRAPPER ===
send_lock = asyncio.Lock()

async def safe_send(bot, chat_id, text):
    async with send_lock:
        try:
            await bot.send_message(chat_id=chat_id, text=text)
            await asyncio.sleep(1.1)
        except RetryAfter as e:
            print(f"⚠️ Flood control hit. Sleeping for {e.retry_after}s...")
            await asyncio.sleep(e.retry_after + 1)
        except Exception as e:
            print(f"❌ Failed to send message: {e}")

# === AUTH DECORATOR ===
def auth_required(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in authorized_users:
            await update.message.reply_text("❌ You are not authorized to use this bot.")
            return
        return await func(update, context)
    return wrapper

# === FETCH FUNCTION ===
async def fetch_test_data(session, nid):
    try:
        async with session.get(f"{API_URL}{nid}", timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, list) and data:
                    return nid, data[0].get("title", "No Title")
    except Exception:
        pass
    return nid, None

# === /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in authorized_users:
        await update.message.reply_text("❌ You are not authorized to use this bot.")
        return

    await update.message.reply_text(
        "👋 *Welcome to the Aakash NID Scanner Bot!*\n\n"
        "This bot scans *test NIDs* on the Aakash iTutor platform and shows only FOUND test names.\n\n"
        "🔧 *How it works:*\n"
        "• Run `/scan <start> <end>`\n"
        "• Bot checks all NIDs in that range\n"
        "• Found tests are sent to Telegram\n"
        "• All NIDs are printed in the console\n"
        "• Use `/status` to check progress\n"
        "• Use `/stop` to cancel the scan\n\n"
        "📋 *Available Commands:*\n"
        "`/scan <start> <end>` — Start scanning\n"
        "`/stop` — Cancel the scan\n"
        "`/status` — Show current progress\n"
        "`/au <user_id>` — Authorize another user\n"
        "`/help` — Show this guide again",
        parse_mode="Markdown"
    )

# === /help ===
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

# === /status ===
@auth_required
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not scan_status["active"]:
        await update.message.reply_text("ℹ️ No active scan.")
        return

    await update.message.reply_text(
        f"📊 *Scan Status:*\n"
        f"🔢 Range: {scan_status['start']} → {scan_status['end']}\n"
        f"📍 Current NID: {scan_status['current']}\n"
        f"🧮 Scanned: {scan_status['scanned']}\n"
        f"✅ Found: {scan_status['found']}",
        parse_mode="Markdown"
    )

# === SCAN FUNCTION ===
async def do_scan(update: Update, msg, start_nid: int, end_nid: int):
    found = 0
    total = 0
    scan_status.update({
        "active": True,
        "start": start_nid,
        "end": end_nid,
        "current": start_nid,
        "scanned": 0,
        "found": 0
    })

    try:
        async with aiohttp.ClientSession() as session:
            for i in range(start_nid, end_nid + 1, BATCH_SIZE):
                batch = range(i, min(i + BATCH_SIZE, end_nid + 1))
                tasks = [fetch_test_data(session, nid) for nid in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for result in results:
                    if isinstance(result, Exception):
                        continue
                    nid, title = result
                    scan_status["current"] = nid
                    total += 1
                    scan_status["scanned"] = total
                    if title:
                        found += 1
                        scan_status["found"] = found
                        await safe_send(update.get_bot(), update.effective_chat.id, f"✅ {title} (NID: {nid})")
                    print(f"{'FOUND' if title else 'NOT FOUND'}: NID {nid}")

                if total % 1000 < BATCH_SIZE:
                    try:
                        await msg.edit_text(
                            f"🔎 Scanning {start_nid} → {end_nid}\nProgress: {total} / {end_nid - start_nid + 1}"
                        )
                    except Exception:
                        pass

        await msg.edit_text(f"✅ Scan complete!\nScanned: {total}, Found: {found}")
    except asyncio.CancelledError:
        await msg.edit_text("🛑 Scan was cancelled.")
        print("🛑 Scan cancelled.")
    finally:
        global scanning_task
        scanning_task = None
        scan_status["active"] = False

# === /scan ===
@auth_required
async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global scanning_task
    if scanning_task and not scanning_task.done():
        await update.message.reply_text("⚠️ A scan is already running. Use /stop to cancel it first.")
        return

    if len(context.args) != 2:
        await update.message.reply_text("Usage: /scan <start_nid> <end_nid>")
        return

    try:
        start_nid = int(context.args[0])
        end_nid = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid NID values.")
        return

    msg = await update.message.reply_text(
        f"🔎 Scanning {start_nid} → {end_nid}\nProgress: 0 / {end_nid - start_nid + 1}"
    )
    scanning_task = asyncio.create_task(do_scan(update, msg, start_nid, end_nid))

# === /stop ===
@auth_required
async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global scanning_task
    if scanning_task and not scanning_task.done():
        scanning_task.cancel()
        await update.message.reply_text("🛑 Stopping the scan...")
    else:
        await update.message.reply_text("⚠️ No active scan to stop.")

# === /au ===
async def auth_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in authorized_users:
        await update.message.reply_text("❌ You're not authorized to give access.")
        return

    if len(context.args) != 1:
        await update.message.reply_text("Usage: /au <user_id>")
        return

    try:
        uid = int(context.args[0])
        authorized_users.add(uid)
        await update.message.reply_text(f"✅ User `{uid}` authorized.", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")

# === MAIN ===
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("scan", scan_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("au", auth_user))

    print("✅ Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
