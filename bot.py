import aiohttp
import asyncio
import logging
import re
from collections import defaultdict
from telegram import Update, constants
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import RetryAfter, TelegramError

# === CONFIG ===
TOKEN = "8380598059:AAFD7pALzpCBo-qXNTizUWnSQE9tFNSi8h4"         # Replace with your actual bot token
OWNER_ID = 7796598050                  # Replace with your Telegram numeric user ID
API_URL = "https://learn.aakashitutor.com/api/getquizfromid?nid="
DEFAULT_BATCH_SIZE = 1000

# === LOGGING ===
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# === GLOBAL STATE ===
ongoing_searches = {}
checked_nid_counts = defaultdict(int)
total_nids_to_check = {}

# === HELPERS ===
def escape_markdown_v2(text: str) -> str:
    return re.sub(r'([_*\[\]()~`>#+=|{}.!\\-])', r'\\\1', text)

async def safe_send(bot_method, *args, **kwargs):
    try:
        return await bot_method(*args, **kwargs)
    except RetryAfter as e:
        logger.warning(f"Flood control hit. Retrying after {e.retry_after} seconds.")
        await asyncio.sleep(e.retry_after)
        return await safe_send(bot_method, *args, **kwargs)
    except TelegramError as e:
        logger.error(f"TelegramError: {e}")
    except Exception as e:
        logger.error(f"Unexpected send error: {e}")
    return None

async def fetch_test_data(session, nid):
    try:
        async with session.get(f"{API_URL}{nid}", timeout=15) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, list) and data and isinstance(data[0], dict):
                    title = data[0].get("title", "No Title")
                    logger.info(f"✅ FOUND: NID {nid} - {title}")
                    return nid, escape_markdown_v2(title)
                else:
                    logger.info(f"❌ NOT FOUND: NID {nid}")
            else:
                logger.warning(f"❌ API error {resp.status} for NID {nid}")
    except Exception as e:
        logger.error(f"❌ Error fetching NID {nid}: {e}")
    return nid, None

# === OWNER-ONLY DECORATOR ===
def owner_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != OWNER_ID:
            await safe_send(update.message.reply_text, "🚫 You are not authorized to use this bot.")
            return
        return await func(update, context)
    return wrapper

# === COMMANDS ===
@owner_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_send(update.message.reply_text,
        "👋 Welcome, owner\\!\nUse `/search <start> <end> [batch_size]` to begin.",
        parse_mode=constants.ParseMode.MARKDOWN_V2)

@owner_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📌 *Bot Commands*\n"
        "`/start` - Welcome message\n"
        "`/search <start> <end> [batch_size]` - Start scanning NIDs\n"
        "`/cancel` - Stop ongoing scan\n"
        "`/status` - Show scan progress\n"
        "`/help` - Show this help"
    )
    await safe_send(update.message.reply_text, help_text, parse_mode=constants.ParseMode.MARKDOWN_V2)

@owner_only
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    current = checked_nid_counts.get(chat_id, 0)
    total = total_nids_to_check.get(chat_id, '?')
    if chat_id in ongoing_searches and not ongoing_searches[chat_id].done():
        await safe_send(update.message.reply_text,
            f"🔄 Progress: `{current}` / `{total}`",
            parse_mode=constants.ParseMode.MARKDOWN_V2)
    else:
        await safe_send(update.message.reply_text, "ℹ️ No active scan running.")

@owner_only
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    task = ongoing_searches.get(chat_id)
    if task and not task.done():
        task.cancel()
        await safe_send(update.message.reply_text, "🛑 Scan cancelled.")
    else:
        await safe_send(update.message.reply_text, "ℹ️ No active scan to cancel.")

@owner_only
async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args

    if len(args) < 2:
        await safe_send(update.message.reply_text, "❗ Usage: /search <start_nid> <end_nid> [batch_size]")
        return

    try:
        start_nid = int(args[0])
        end_nid = int(args[1])
        batch_size = int(args[2]) if len(args) > 2 else DEFAULT_BATCH_SIZE

        if start_nid > end_nid:
            await safe_send(update.message.reply_text, "⚠️ Start NID must be less than or equal to End NID.")
            return

        checked_nid_counts[chat_id] = 0
        total_nids_to_check[chat_id] = end_nid - start_nid + 1

        task = asyncio.create_task(
            perform_search(chat_id, start_nid, end_nid, batch_size, context)
        )
        ongoing_searches[chat_id] = task

    except ValueError:
        await safe_send(update.message.reply_text, "❗ Invalid NID or batch size.")

# === SEARCH TASK ===
async def perform_search(chat_id, start_nid, end_nid, batch_size, context):
    total = end_nid - start_nid + 1
    await safe_send(context.bot.send_chat_action, chat_id=chat_id, action=constants.ChatAction.TYPING)

    intro_msg = await safe_send(context.bot.send_message, chat_id=chat_id,
        text=f"🔍 Starting scan from `{start_nid}` to `{end_nid}`\nTotal NIDs: `{total}`",
        parse_mode=constants.ParseMode.MARKDOWN_V2)

    try:
        async with aiohttp.ClientSession() as session:
            for i in range(start_nid, end_nid + 1, batch_size):
                if chat_id not in ongoing_searches or ongoing_searches[chat_id].cancelled():
                    await safe_send(context.bot.send_message, chat_id=chat_id, text="🛑 Scan cancelled.")
                    return

                batch = range(i, min(i + batch_size, end_nid + 1))
                results = await asyncio.gather(*(fetch_test_data(session, nid) for nid in batch))

                found_msgs = []
                for result in results:
                    if isinstance(result, tuple):
                        nid, title = result
                        checked_nid_counts[chat_id] += 1
                        if title:
                            found_msgs.append(f"✅ *Found*: {title} \\(NID: `{nid}`\\)")
                        else:
                            logger.info(f"❌ Not found: NID {nid}")
                    elif isinstance(result, Exception):
                        logger.error(f"⚠️ Error in batch: {result}")

                if found_msgs:
                    await safe_send(context.bot.send_message, chat_id=chat_id,
                                    text="\n".join(found_msgs), parse_mode=constants.ParseMode.MARKDOWN_V2)

                if checked_nid_counts[chat_id] % 1000 == 0:
                    await safe_send(intro_msg.edit_text,
                        text=f"🔄 Progress: `{checked_nid_counts[chat_id]}` / `{total}`",
                        parse_mode=constants.ParseMode.MARKDOWN_V2)

        await safe_send(context.bot.send_message, chat_id=chat_id,
            text=f"✅ Done\\! Checked `{checked_nid_counts[chat_id]}` NIDs.",
            parse_mode=constants.ParseMode.MARKDOWN_V2)

    finally:
        ongoing_searches.pop(chat_id, None)
        checked_nid_counts.pop(chat_id, None)
        total_nids_to_check.pop(chat_id, None)

# === MAIN ===
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("search", search))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("status", status))

    logger.info(f"🚀 Bot started for Owner ID: {OWNER_ID}")
    app.run_polling()

if __name__ == "__main__":
    main()
