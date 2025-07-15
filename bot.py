import aiohttp
import asyncio
import os
import logging
from telegram import Update, constants
from telegram.ext import Application, CommandHandler, ContextTypes
from collections import defaultdict
import re  # Import re for regular expressions
import time  # Added for time estimation

# === CONFIG ===
TOKEN = "8134070148:AAFForE3AUaJg4rJdlIaeX_A3AnG-Ld9mmY"
OWNER_ID = 7796598050  # <-- Replace with your actual Telegram user ID
API_URL = "https://learn.aakashitutor.com/api/getquizfromid?nid="
DEFAULT_BATCH_SIZE = 500  # Default safe value

# === LOGGING ===
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
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
    except aiohttp.ClientError as e:
        logger.warning(f"Network error fetching NID {nid}: {e}")
    except asyncio.TimeoutError:
        logger.warning(f"Timeout fetching NID {nid}")
    except Exception as e:
        logger.error(f"Unexpected error fetching NID {nid}: {e}")
    return nid, None

async def perform_search(chat_id: int, start_nid: int, end_nid: int, batch_size: int, context: ContextTypes.DEFAULT_TYPE):
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
                f"🔍 Starting NID search from `{start_nid}` to `{end_nid}`\. "
                f"Total NIDs to check: `{total_nids}`\.\n"
                f"Progress: `0` / `{total_nids}` completed\."
            ),
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )

        async with aiohttp.ClientSession() as session:
            for i in range(start_nid, end_nid + 1, batch_size):
                if chat_id not in ongoing_searches or ongoing_searches[chat_id].done():
                    logger.info(f"Search for chat {chat_id} cancelled or finished externally.")
                    await context.bot.send_message(chat_id=chat_id, text="⏹️ Search cancelled\.", parse_mode=constants.ParseMode.MARKDOWN_V2)
                    return

                batch_end = min(i + batch_size - 1, end_nid)
                batch = range(i, batch_end + 1)
                tasks = [fetch_test_data(session, nid) for nid in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                valid_nids_found_in_batch = []
                for result in results:
                    if isinstance(result, Exception):
                        logger.debug(f"Skipping exception in batch: {result}")
                        continue

                    nid, title = result
                    checked_nid_counts[chat_id] += 1
                    if title:
                        found_counts[chat_id] += 1
                        valid_nids_found_in_batch.append(f"✅ Found: {title} \(NID: `{nid}`\)")
                        logger.info(f"FOUND: NID {nid} - {title}")

                if valid_nids_found_in_batch:
                    response_text = "\n".join(valid_nids_found_in_batch)
                    await context.bot.send_message(chat_id=chat_id, text=response_text, parse_mode=constants.ParseMode.MARKDOWN_V2)

                if checked_nid_counts[chat_id] % 500 == 0 or (batch_end == end_nid):
                    if message:
                        current_checked = checked_nid_counts[chat_id]
                        total_nids_val = total_nids_to_check.get(chat_id, total_nids)

                        await message.edit_text(
                            f"🔍 Searching NIDs from `{start_nid}` to `{end_nid}`\.\n"
                            f"Progress: `{current_checked}` / `{total_nids_val}` completed\.",
                            parse_mode=constants.ParseMode.MARKDOWN_V2
                        )
                    else:
                        message = await context.bot.send_message(
                            chat_id=chat_id,
                            text=(
                                f"🔍 Searching NIDs from `{start_nid}` to `{end_nid}`\.\n"
                                f"Progress: `{checked_nid_counts[chat_id]}` / `{total_nids}` completed\."
                            ),
                            parse_mode=constants.ParseMode.MARKDOWN_V2
                        )

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ Search complete\! Total NIDs checked: `{checked_nid_counts[chat_id]}`\.",
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )
        logger.info(f"Search for chat {chat_id} from {start_nid} to {end_nid} completed.")

    except asyncio.CancelledError:
        await context.bot.send_message(chat_id=chat_id, text="⏹️ Search gracefully cancelled\.", parse_mode=constants.ParseMode.MARKDOWN_V2)
        logger.info(f"Search task for chat {chat_id} was cancelled.")
    except Exception as e:
        logger.error(f"Error during search for chat {chat_id}: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text=f"❌ An error occurred during the search: `{escape_markdown_v2(str(e))}`", parse_mode=constants.ParseMode.MARKDOWN_V2)
    finally:
        if chat_id in ongoing_searches:
            del ongoing_searches[chat_id]
        if chat_id in checked_nid_counts:
            del checked_nid_counts[chat_id]
        if chat_id in total_nids_to_check:
            del total_nids_to_check[chat_id]
        if chat_id in start_times:
            del start_times[chat_id]
        if chat_id in found_counts:
            del found_counts[chat_id]
        if chat_id in search_ranges:
            del search_ranges[chat_id]
        if message:
            try:
                if "Progress:" in message.text:
                    final_checked = checked_nid_counts.get(chat_id, 0)
                    final_total = total_nids_to_check.get(chat_id, total_nids)
                    await message.edit_text(
                        f"✅ Search session ended\. Total NIDs checked: `{final_checked}` / `{final_total}`\.",
                        parse_mode=constants.ParseMode.MARKDOWN_V2
                    )
            except Exception as e:
                logger.warning(f"Could not edit final status message for chat {chat_id}: {e}")

# === Telegram Command Handlers ===

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome\! I can help you search for NIDs on Aakash iTutor\.\n\n"
        "Here are the commands you can use:\n"
        "• `/search <start_nid> <end_nid>`: Search for NIDs within a specified range\.\n"
        "Example: `/search 4379492956 4379493000`\n"
        "• `/cancel`: Stop any ongoing NID search\.\n"
        "• `/status`: Get the current status of your ongoing search, if any\.\n"
        "• `/help`: Show this help message again\.",
        parse_mode=constants.ParseMode.MARKDOWN_V2
    )

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ...

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ...

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        await update.message.reply_text("🚫 Only the bot owner can use this command.", parse_mode=constants.ParseMode.MARKDOWN_V2)
        return

    msg_lines = []
    active_users = list(ongoing_searches.keys())
    msg_lines.append(f"📊 *Active Scans:* `{len(active_users)}`")

    for cid in active_users:
        checked = checked_nid_counts.get(cid, 0)
        total = total_nids_to_check.get(cid, 0)
        found = found_counts.get(cid, 0)
        start_nid, end_nid = search_ranges.get(cid, ("?", "?"))
        elapsed = time.time() - start_times.get(cid, time.time())
        rate = checked / elapsed if elapsed > 0 else 0
        remaining = total - checked
        eta = remaining / rate if rate > 0 else 0

        msg_lines.append(
            f"\n🔹 *Scan*\n"
            f"• From: `{start_nid}`\n"
            f"• To: `{end_nid}`\n"
            f"• Current: `{start_nid + checked}`\n"
            f"• Checked: `{checked}` / `{total}`\n"
            f"• Found: `{found}`\n"
            f"• ETA: `{int(eta)}s`")

    await update.message.reply_text("\n".join(msg_lines), parse_mode=constants.ParseMode.MARKDOWN_V2)

# === Main function ===
def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", start_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("status", status_command))
    logger.info("🚀 Bot is starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
