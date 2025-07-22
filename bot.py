import aiohttp
import asyncio
import os
import logging
from telegram import Update, constants
from telegram.ext import Application, CommandHandler, ContextTypes
from collections import defaultdict
import re
from telegram.error import RetryAfter

# === CONFIG ===
TOKEN = "7622336683:AAFBxrx1hPuG_5ZNY14zQjrxzRgPaS_Jf5A"  # Replace with your actual bot token
API_URL = "https://learn.aakashitutor.com/api/getquizfromid?nid="
DEFAULT_BATCH_SIZE = 500

# === LOGGING ===
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# === GLOBAL STATE ===
ongoing_searches = {}
checked_nid_counts = defaultdict(int)
total_nids_to_check = {}

# === Helper Functions ===
def escape_markdown_v2(text: str) -> str:
    special_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f"([{re.escape(special_chars)}])", r"\\\1", text)

async def safe_send(bot_method, *args, **kwargs):
    try:
        return await bot_method(*args, **kwargs)
    except RetryAfter as e:
        logger.warning(f"Flood control hit. Retrying after {e.retry_after} seconds.")
        await asyncio.sleep(e.retry_after)
        return await safe_send(bot_method, *args, **kwargs)
    except Exception as e:
        logger.error(f"Error during safe_send: {e}")
        return None

async def fetch_test_data(session, nid):
    try:
        async with session.get(f"{API_URL}{nid}", timeout=15) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, list) and data:
                    title = data[0].get("title", "No Title")
                    logger.info(f"✅ FOUND: NID {nid} - {title}")
                    return nid, escape_markdown_v2(title)
                else:
                    logger.info(f"❌ NOT FOUND: NID {nid}")
            else:
                logger.warning(f"API error for NID {nid} with status code {resp.status}")
    except Exception as e:
        logger.warning(f"Error fetching NID {nid}: {e}")
    return nid, None

async def perform_search(chat_id, start_nid, end_nid, batch_size, context):
    message = None
    total_nids = end_nid - start_nid + 1
    total_nids_to_check[chat_id] = total_nids

    try:
        await safe_send(context.bot.send_chat_action, chat_id=chat_id, action=constants.ChatAction.TYPING)
        message = await safe_send(
            context.bot.send_message,
            chat_id=chat_id,
            text=f"🔍 Starting NID search from `{start_nid}` to `{end_nid}`\. Total NIDs to check: `{total_nids}`\.",
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )

        async with aiohttp.ClientSession() as session:
            for i in range(start_nid, end_nid + 1, batch_size):
                if chat_id not in ongoing_searches or ongoing_searches[chat_id].done():
                    await safe_send(context.bot.send_message, chat_id=chat_id, text="⏹️ Search cancelled\.", parse_mode=constants.ParseMode.MARKDOWN_V2)
                    return

                batch_end = min(i + batch_size - 1, end_nid)
                batch = range(i, batch_end + 1)
                tasks = [fetch_test_data(session, nid) for nid in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                found_messages = []
                for result in results:
                    if isinstance(result, Exception):
                        logger.error(f"❌ Error in batch: {result}")
                        continue
                    nid, title = result
                    checked_nid_counts[chat_id] += 1
                    if title:
                        found_messages.append(f"✅ Found: {title} \(NID: `{nid}`\)")

                if found_messages:
                    await safe_send(context.bot.send_message, chat_id=chat_id, text="\n".join(found_messages), parse_mode=constants.ParseMode.MARKDOWN_V2)

                if message and checked_nid_counts[chat_id] % 500 == 0:
                    await safe_send(message.edit_text, f"🔍 Progress: `{checked_nid_counts[chat_id]}` / `{total_nids}` completed\.", parse_mode=constants.ParseMode.MARKDOWN_V2)

        await safe_send(context.bot.send_message, chat_id=chat_id, text=f"✅ Search complete\! Total NIDs checked: `{checked_nid_counts[chat_id]}`\.", parse_mode=constants.ParseMode.MARKDOWN_V2)

    except asyncio.CancelledError:
        await safe_send(context.bot.send_message, chat_id=chat_id, text="⏹️ Search gracefully cancelled\.", parse_mode=constants.ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"❌ Error in perform_search: {e}", exc_info=True)
        await safe_send(context.bot.send_message, chat_id=chat_id, text=f"❌ Error: `{escape_markdown_v2(str(e))}`", parse_mode=constants.ParseMode.MARKDOWN_V2)
    finally:
        ongoing_searches.pop(chat_id, None)
        checked_nid_counts.pop(chat_id, None)
        total_nids_to_check.pop(chat_id, None)

# === Command Handlers ===

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_send(update.message.reply_text,
        "👋 Welcome\! Use `/search <start> <end>` to begin searching for valid NIDs\.",
        parse_mode=constants.ParseMode.MARKDOWN_V2)

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args

    if chat_id in ongoing_searches and not ongoing_searches[chat_id].done():
        await safe_send(update.message.reply_text, "⏳ A search is already running\. Use /cancel to stop it\.", parse_mode=constants.ParseMode.MARKDOWN_V2)
        return

    if len(args) < 2:
        await safe_send(update.message.reply_text, "Usage: `/search <start_nid> <end_nid>`", parse_mode=constants.ParseMode.MARKDOWN_V2)
        return

    try:
        start_nid = int(args[0])
        end_nid = int(args[1])
        batch_size = int(args[2]) if len(args) > 2 else DEFAULT_BATCH_SIZE

        if start_nid > end_nid:
            await safe_send(update.message.reply_text, "Start NID must be less than or equal to End NID\.", parse_mode=constants.ParseMode.MARKDOWN_V2)
            return

        checked_nid_counts[chat_id] = 0
        total_nids_to_check[chat_id] = end_nid - start_nid + 1
        task = asyncio.create_task(perform_search(chat_id, start_nid, end_nid, batch_size, context))
        ongoing_searches[chat_id] = task

    except ValueError:
        await safe_send(update.message.reply_text, "Invalid NID or batch size\. Use numbers only\.", parse_mode=constants.ParseMode.MARKDOWN_V2)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in ongoing_searches:
        ongoing_searches[chat_id].cancel()
        await safe_send(update.message.reply_text, "⏹️ Cancelled the search\.", parse_mode=constants.ParseMode.MARKDOWN_V2)
    else:
        await safe_send(update.message.reply_text, "No active search to cancel\.", parse_mode=constants.ParseMode.MARKDOWN_V2)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in ongoing_searches and not ongoing_searches[chat_id].done():
        current = checked_nid_counts.get(chat_id, 0)
        total = total_nids_to_check.get(chat_id, '?')
        await safe_send(update.message.reply_text, f"🔄 Progress: `{current}` / `{total}` NIDs checked\.", parse_mode=constants.ParseMode.MARKDOWN_V2)
    else:
        await safe_send(update.message.reply_text, "No active search running\.", parse_mode=constants.ParseMode.MARKDOWN_V2)

# === Main ===
def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("status", status_command))

    logger.info("🚀 Bot started")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
