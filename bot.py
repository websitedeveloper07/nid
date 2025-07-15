import aiohttp
import asyncio
import logging
from telegram import Update, constants
from telegram.ext import Application, CommandHandler, ContextTypes
from collections import defaultdict
import re
import time

# === CONFIG ===
TOKEN = "8134070148:AAFForE3AUaJg4rJdlIaeX_A3AnG-Ld9mmY"
OWNER_ID = 7796598050  # <-- Replace with your Telegram user ID
API_URL = "https://learn.aakashitutor.com/api/getquizfromid?nid="
DEFAULT_BATCH_SIZE = 500

# === LOGGING ===
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# === GLOBAL STATE ===
ongoing_searches = {}
checked_nid_counts = defaultdict(int)
total_nids_to_check = {}
start_times = {}
found_counts = defaultdict(int)
search_ranges = {}

# === Helper Functions ===
def escape_markdown_v2(text: str) -> str:
    special_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f"([{re.escape(special_chars)}])", r"\\\1", text)

async def fetch_test_data(session, nid):
    try:
        async with session.get(f"{API_URL}{nid}", timeout=15) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, list) and data:
                    title = data[0].get("title", "No Title")
                    return nid, escape_markdown_v2(title)
            else:
                logger.debug(f"API returned status {resp.status} for NID {nid}")
    except Exception as e:
        logger.warning(f"Error fetching NID {nid}: {e}")
    return nid, None

async def perform_search(chat_id, start_nid, end_nid, batch_size, context):
    message = None
    total_nids = end_nid - start_nid + 1
    total_nids_to_check[chat_id] = total_nids
    start_times[chat_id] = time.time()
    search_ranges[chat_id] = (start_nid, end_nid)

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.TYPING)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🔍 Starting NID search from `{start_nid}` to `{end_nid}`. "
                f"Total NIDs to check: `{total_nids}`.\n"
                f"Progress: `0` / `{total_nids}` completed."
            ),
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )

        async with aiohttp.ClientSession() as session:
            for i in range(start_nid, end_nid + 1, batch_size):
                if chat_id not in ongoing_searches or ongoing_searches[chat_id].done():
                    await context.bot.send_message(chat_id=chat_id, text="⏹️ Search cancelled.", parse_mode=constants.ParseMode.MARKDOWN_V2)
                    return

                batch_end = min(i + batch_size - 1, end_nid)
                results = await asyncio.gather(*[fetch_test_data(session, nid) for nid in range(i, batch_end + 1)], return_exceptions=True)

                found_batch = []
                for result in results:
                    if isinstance(result, Exception):
                        continue
                    nid, title = result
                    checked_nid_counts[chat_id] += 1
                    if title:
                        found_counts[chat_id] += 1
                        found_batch.append(f"✅ Found: {title} (NID: `{nid}`)")

                if found_batch:
                    await context.bot.send_message(chat_id=chat_id, text="\n".join(found_batch), parse_mode=constants.ParseMode.MARKDOWN_V2)

                if checked_nid_counts[chat_id] % 500 == 0 or batch_end == end_nid:
                    current = checked_nid_counts[chat_id]
                    total = total_nids_to_check[chat_id]
                    if message:
                        await message.edit_text(
                            f"🔍 Searching NIDs from `{start_nid}` to `{end_nid}`.\n"
                            f"Progress: `{current}` / `{total}` completed.",
                            parse_mode=constants.ParseMode.MARKDOWN_V2
                        )
        await context.bot.send_message(chat_id=chat_id, text=f"✅ Search complete! Total NIDs checked: `{checked_nid_counts[chat_id]}`.", parse_mode=constants.ParseMode.MARKDOWN_V2)
    except asyncio.CancelledError:
        await context.bot.send_message(chat_id=chat_id, text="⏹️ Search gracefully cancelled.", parse_mode=constants.ParseMode.MARKDOWN_V2)
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Error: `{escape_markdown_v2(str(e))}`", parse_mode=constants.ParseMode.MARKDOWN_V2)
    finally:
        for key in [chat_id]:
            ongoing_searches.pop(key, None)
            checked_nid_counts.pop(key, None)
            total_nids_to_check.pop(key, None)
            start_times.pop(key, None)
            found_counts.pop(key, None)
            search_ranges.pop(key, None)

# === Telegram Command Handlers ===
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! I can help you search for NIDs on Aakash iTutor.\n\n"
        "• `/search <start_nid> <end_nid> [batch_size]`\n"
        "• `/cancel`\n"
        "• `/status`\n"
        "• `/help`",
        parse_mode=constants.ParseMode.MARKDOWN_V2
    )

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"/search called by {update.effective_user.id}, args={context.args}")
    chat_id = update.effective_chat.id

    if chat_id in ongoing_searches and not ongoing_searches[chat_id].done():
        await update.message.reply_text("⏳ A search is already running. Use /cancel to stop it.", parse_mode=constants.ParseMode.MARKDOWN_V2)
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: `/search <start_nid> <end_nid> [batch_size]`", parse_mode=constants.ParseMode.MARKDOWN_V2)
        return

    try:
        start_nid, end_nid = map(int, context.args[:2])
        batch_size = int(context.args[2]) if len(context.args) > 2 else DEFAULT_BATCH_SIZE
        checked_nid_counts[chat_id] = 0
        total_nids_to_check[chat_id] = end_nid - start_nid + 1
        ongoing_searches[chat_id] = asyncio.create_task(perform_search(chat_id, start_nid, end_nid, batch_size, context))
        logger.info(f"Started search for chat {chat_id}: {start_nid}-{end_nid}")
    except Exception:
        await update.message.reply_text("❌ Please provide valid integer NIDs and optional batch size.", parse_mode=constants.ParseMode.MARKDOWN_V2)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    task = ongoing_searches.get(chat_id)
    if task and not task.done():
        task.cancel()
        await update.message.reply_text("⏹️ Cancelling your search...", parse_mode=constants.ParseMode.MARKDOWN_V2)
    else:
        await update.message.reply_text("🔍 No active search to cancel.", parse_mode=constants.ParseMode.MARKDOWN_V2)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("🚫 Only the bot owner can use this command.", parse_mode=constants.ParseMode.MARKDOWN_V2)
        return

    tasks = list(ongoing_searches.keys())
    await update.message.reply_text(f"📊 Active scans: `{len(tasks)}`", parse_mode=constants.ParseMode.MARKDOWN_V2)
    for chat_id in tasks:
        checked = checked_nid_counts.get(chat_id, 0)
        total = total_nids_to_check.get(chat_id, 0)
        found = found_counts.get(chat_id, 0)
        s, e = search_ranges.get(chat_id, ("?", "?"))
        elapsed = time.time() - start_times.get(chat_id, 0)
        rate = checked / elapsed if elapsed > 0 else 0
        eta = int((total - checked) / rate) if rate > 0 else 0
        await update.message.reply_text(
            f"🔹 Range: `{s}`–`{e}`\n"
            f"• Current: `{s + checked}`\n"
            f"• Checked: `{checked}` / `{total}`\n"
            f"• Found: `{found}`\n"
            f"• ETA: `{eta}s`",
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler(["start", "help"], start_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("status", status_command))
    logger.info("🚀 Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
