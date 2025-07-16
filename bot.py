import aiohttp
import asyncio
import os
import logging
from telegram import Update, constants
from telegram.ext import Application, CommandHandler, ContextTypes
from collections import defaultdict
import re

# === CONFIG ===
TOKEN = "8134070148:AAFForE3AUaJg4rJdlIaeX_A3AnG-Ld9mmY"
API_URL = "https://learn.aakashitutor.com/api/getquizfromid?nid="
DEFAULT_BATCH_SIZE = 500

# === AUTH ===
AUTHORIZED_USERS = {7796598050}  # Replace with your allowed user IDs

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
current_nid_tracking = {}  # Track currently processing NID per chat

# === Helper Functions ===
def is_authorized(user_id: int) -> bool:
    return user_id in AUTHORIZED_USERS

async def reject_unauthorized(update: Update):
    await update.message.reply_text("\ud83d\udeab You are not authorized to use this bot.")

async def fetch_test_data(session, nid):
    try:
        async with session.get(f"{API_URL}{nid}", timeout=15) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, list) and data:
                    title = data[0].get("title", "No Title")
                    return nid, title
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

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.TYPING)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text=f"\ud83d\udd0d Starting NID search from <code>{start_nid}</code> to <code>{end_nid}</code>. Total NIDs to check: <code>{total_nids}</code>.<br>"
                 f"Progress: <code>0</code> / <code>{total_nids}</code> completed.",
            parse_mode=constants.ParseMode.HTML
        )

        async with aiohttp.ClientSession() as session:
            for i in range(start_nid, end_nid + 1, batch_size):
                if chat_id not in ongoing_searches or ongoing_searches[chat_id].done():
                    logger.info(f"Search for chat {chat_id} cancelled or finished externally.")
                    await context.bot.send_message(chat_id=chat_id, text="\u23f9\ufe0f Search cancelled.", parse_mode=constants.ParseMode.HTML)
                    return

                batch_end = min(i + batch_size - 1, end_nid)
                batch = range(i, batch_end + 1)
                tasks = [fetch_test_data(session, nid) for nid in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                valid_nids_found_in_batch = []
                for result in results:
                    if isinstance(result, Exception):
                        continue

                    nid, title = result
                    checked_nid_counts[chat_id] += 1
                    current_nid_tracking[chat_id] = nid

                    if title:
                        valid_nids_found_in_batch.append(f"\u2705 Found: {title} (NID: <code>{nid}</code>)")
                        logger.info(f"FOUND: NID {nid} - {title}")

                if valid_nids_found_in_batch:
                    response_text = "<br>".join(valid_nids_found_in_batch)
                    await context.bot.send_message(chat_id=chat_id, text=response_text, parse_mode=constants.ParseMode.HTML)

                if checked_nid_counts[chat_id] % 500 == 0 or (batch_end == end_nid):
                    if message:
                        await message.edit_text(
                            f"\ud83d\udd0d Searching NIDs from <code>{start_nid}</code> to <code>{end_nid}</code>.<br>"
                            f"Progress: <code>{checked_nid_counts[chat_id]}</code> / <code>{total_nids}</code> completed.",
                            parse_mode=constants.ParseMode.HTML
                        )

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"\u2705 Search complete! Total NIDs checked: <code>{checked_nid_counts[chat_id]}</code>.",
            parse_mode=constants.ParseMode.HTML
        )

    except asyncio.CancelledError:
        await context.bot.send_message(chat_id=chat_id, text="\u23f9\ufe0f Search gracefully cancelled.", parse_mode=constants.ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error during search for chat {chat_id}: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text=f"\u274c An error occurred during the search: <code>{str(e)}</code>", parse_mode=constants.ParseMode.HTML)
    finally:
        ongoing_searches.pop(chat_id, None)
        checked_nid_counts.pop(chat_id, None)
        total_nids_to_check.pop(chat_id, None)
        current_nid_tracking.pop(chat_id, None)

        if message:
            try:
                await message.edit_text(
                    f"\u2705 Search session ended. Total NIDs checked: <code>{checked_nid_counts.get(chat_id, 0)}</code>.",
                    parse_mode=constants.ParseMode.HTML
                )
            except Exception as e:
                logger.warning(f"Could not edit final status message for chat {chat_id}: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 <b>Welcome!</b> I can help you search for NIDs on Aakash iTutor.<br><br>"
        "<b>Here are the commands you can use:</b><br>"
        "• <code>/search &lt;start_nid&gt; &lt;end_nid&gt;</code>: Search for NIDs within a specified range.<br>"
        "Example: <code>/search 4379492956 4379493000</code><br>"
        "• <code>/cancel</code>: Stop any ongoing NID search.<br>"
        "• <code>/status</code>: Get the current status of your ongoing search.<br>"
        "• <code>/help</code>: Show this help message again.",
        parse_mode=constants.ParseMode.HTML
    )



async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await reject_unauthorized(update)
        return

    chat_id = update.effective_chat.id

    if chat_id in ongoing_searches and not ongoing_searches[chat_id].done():
        await update.message.reply_text("⏳ You already have an active search running. Please /cancel it first.", parse_mode=constants.ParseMode.HTML)
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: <code>/search &lt;start_nid&gt; &lt;end_nid&gt; [batch_size]</code><br>Example: <code>/search 4379492956 4379493000</code>",
            parse_mode=constants.ParseMode.HTML
        )
        return

    try:
        start_nid = int(args[0])
        end_nid = int(args[1])
        batch_size = int(args[2]) if len(args) > 2 else DEFAULT_BATCH_SIZE

        if not (1 <= batch_size <= 10000):
            await update.message.reply_text("Batch size must be between 1 and 10000.", parse_mode=constants.ParseMode.HTML)
            return

        if not (1000000000 <= start_nid <= 9999999999) or not (1000000000 <= end_nid <= 9999999999):
            await update.message.reply_text("NID values must be 10 digits.", parse_mode=constants.ParseMode.HTML)
            return

        if start_nid > end_nid:
            await update.message.reply_text("start_nid cannot be greater than end_nid.", parse_mode=constants.ParseMode.HTML)
            return

        if end_nid - start_nid + 1 > 50000000:
            await update.message.reply_text("NID range too large. Limit to 50,000,000 at a time.", parse_mode=constants.ParseMode.HTML)
            return

        checked_nid_counts[chat_id] = 0
        total_nids_to_check[chat_id] = end_nid - start_nid + 1
        task = asyncio.create_task(perform_search(chat_id, start_nid, end_nid, batch_size, context))
        ongoing_searches[chat_id] = task

    except ValueError:
        await update.message.reply_text("Please provide valid numerical NID values.", parse_mode=constants.ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error in search_command: {e}", exc_info=True)
        await update.message.reply_text(f"Unexpected error: <code>{str(e)}</code>", parse_mode=constants.ParseMode.HTML)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await reject_unauthorized(update)
        return

    chat_id = update.effective_chat.id
    if chat_id in ongoing_searches and not ongoing_searches[chat_id].done():
        ongoing_searches[chat_id].cancel()
        await update.message.reply_text("Cancelling your ongoing NID search...", parse_mode=constants.ParseMode.HTML)
    else:
        await update.message.reply_text("No active NID search found.", parse_mode=constants.ParseMode.HTML)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await reject_unauthorized(update)
        return

    chat_id = update.effective_chat.id
    if chat_id in ongoing_searches and not ongoing_searches[chat_id].done():
        checked_count = checked_nid_counts.get(chat_id, 0)
        total_count = total_nids_to_check.get(chat_id, "N/A")
        current_nid = current_nid_tracking.get(chat_id, "N/A")
        await update.message.reply_text(
            f"\ud83d\udd0d An NID search is active.<br>Checked: <code>{checked_count}</code> / <code>{total_count}</code><br>Currently on NID: <code>{current_nid}</code>",
            parse_mode=constants.ParseMode.HTML
        )
    else:
        await update.message.reply_text("No active NID search.", parse_mode=constants.ParseMode.HTML)

# === Main ===
def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", start_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("status", status_command))

    logger.info("\ud83d\ude80 Bot is starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
