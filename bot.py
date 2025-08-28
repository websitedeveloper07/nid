import aiohttp
import asyncio
import logging
import re
import os  # Import the os module to access environment variables
from collections import defaultdict
from telegram import Update, constants
from telegram.ext import Application, CommandHandler, ContextTypes, filters
from telegram.error import RetryAfter, TelegramError

# === CONFIG ===
# Read the bot token from the environment variable 'BOT_TOKEN'
# Ensure you set this variable in your Railway project settings!
TOKEN = "8337213161:AAGC0grEHd4MSZS2IfzPFIuyQ1fohUsy3vc"
OWNER_ID = 8493360284  # Replace with your Telegram numeric user ID
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
    """Escapes characters in a string that have special meaning in MarkdownV2."""
    # List of special characters in MarkdownV2 that need to be escaped
    # _ * [ ] ( ) ~ > # + - = | { } . ! \
    return re.sub(r'([_*\[\]()~>#+\-=|{}.!\\])', r'\\\1', text)

async def safe_send(bot_method, *args, **kwargs):
    """ Safely sends a message, handling Telegram's flood control and other errors. """
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
    """ Fetches test data for a given NID from the API. Returns (nid, title) if found, otherwise (nid, None). """
    try:
        async with session.get(f"{API_URL}{nid}", timeout=15) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, list) and data and isinstance(data[0], dict):
                    title = data[0].get("title", "No Title")
                    logger.info(f"✅ FOUND: NID {nid} - {title}")
                    # Escape title before returning to ensure it's safe for MarkdownV2
                    return nid, escape_markdown_v2(title)
                else:
                    logger.info(f"❌ NOT FOUND: NID {nid} - API returned non-list or empty data")
            else:
                logger.warning(f"❌ API error {resp.status} for NID {nid}")
    except aiohttp.ClientError as e:
        logger.error(f"❌ Network error fetching NID {nid}: {e}")
    except asyncio.TimeoutError:
        logger.error(f"❌ Timeout fetching NID {nid}")
    except Exception as e:
        logger.error(f"❌ Unexpected error fetching NID {nid}: {e}")
    return nid, None

from telegram import Update, constants
from telegram.ext import ContextTypes

# === COMMANDS (No direct decorators needed here, filters applied in main) ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a welcome message to the owner."""
    await safe_send(
        update.message.reply_text,
        "👋 Welcome, owner!\nUse <code>/search &lt;start&gt; &lt;end&gt; [batch_size]</code> to begin.",
        parse_mode=constants.ParseMode.HTML
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends the help message."""
    help_text = (
        "📌 <b>Bot Commands</b>\n"
        "/start – Welcome message\n"
        "/search &lt;start&gt; &lt;end&gt; [batch_size] – Start scanning NIDs\n"
        "/cancel – Stop ongoing scan\n"
        "/status – Show scan progress\n"
        "/help – Show this help"
    )
    await safe_send(
        update.message.reply_text,
        help_text,
        parse_mode=constants.ParseMode.HTML
    )



async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the current scan progress."""
    chat_id = update.effective_chat.id
    current = checked_nid_counts.get(chat_id, 0)
    total = total_nids_to_check.get(chat_id, '?')
    if chat_id in ongoing_searches and not ongoing_searches[chat_id].done():
        await safe_send(update.message.reply_text, f"🔄 Progress: {current} / {total}", parse_mode=constants.ParseMode.MARKDOWN_V2)
    else:
        await safe_send(update.message.reply_text, "ℹ️ No active scan running\\.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels an ongoing scan."""
    chat_id = update.effective_chat.id
    task = ongoing_searches.get(chat_id)
    if task and not task.done():
        task.cancel()
        await safe_send(update.message.reply_text, "🛑 Scan cancelled\\.")
    else:
        await safe_send(update.message.reply_text, "ℹ️ No active scan to cancel\\.")

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts scanning NIDs within a specified range."""
    chat_id = update.effective_chat.id
    args = context.args
    if len(args) < 2:
        await safe_send(update.message.reply_text, "❗ Usage: /search <start_nid> <end_nid> [batch_size]", parse_mode=constants.ParseMode.MARKDOWN_V2)
        return
    try:
        start_nid = int(args[0])
        end_nid = int(args[1])
        batch_size = int(args[2]) if len(args) > 2 else DEFAULT_BATCH_SIZE
        if start_nid <= 0 or end_nid <= 0:
            await safe_send(update.message.reply_text, "⚠️ NID values must be positive integers\\.")
            return
        if start_nid > end_nid:
            await safe_send(update.message.reply_text, "⚠️ Start NID must be less than or equal to End NID\\.")
            return
        # Check if a search is already ongoing for this chat
        if chat_id in ongoing_searches and not ongoing_searches[chat_id].done():
            await safe_send(update.message.reply_text, "⏳ A scan is already running\\. Use /cancel to stop it\\.", parse_mode=constants.ParseMode.MARKDOWN_V2)
            return
        checked_nid_counts[chat_id] = 0
        total_nids_to_check[chat_id] = end_nid - start_nid + 1
        task = asyncio.create_task(
            perform_search(chat_id, start_nid, end_nid, batch_size, context)
        )
        ongoing_searches[chat_id] = task
    except ValueError:
        await safe_send(update.message.reply_text, "❗ Invalid NID or batch size\\. Please use integers\\.")

# === SEARCH TASK ===
async def perform_search(chat_id, start_nid, end_nid, batch_size, context):
    """Performs the asynchronous NID scanning."""
    total = end_nid - start_nid + 1
    # Send a typing action to indicate the bot is working
    await safe_send(context.bot.send_chat_action, chat_id=chat_id, action=constants.ChatAction.TYPING)
    intro_msg = await safe_send(
        context.bot.send_message,
        chat_id=chat_id,
        text=f"🔍 Starting scan from {start_nid} to {end_nid}\nTotal NIDs: {total}",
        parse_mode=constants.ParseMode.MARKDOWN_V2
    )
    try:
        async with aiohttp.ClientSession() as session:
            for i in range(start_nid, end_nid + 1, batch_size):
                # Check for cancellation before processing each batch
                if chat_id not in ongoing_searches or ongoing_searches[chat_id].cancelled():
                    await safe_send(context.bot.send_message, chat_id=chat_id, text="🛑 Scan cancelled by user\\.")
                    return
                batch = range(i, min(i + batch_size, end_nid + 1))
                # Use asyncio.gather for concurrent fetching of NIDs in the batch
                results = await asyncio.gather(*(fetch_test_data(session, nid) for nid in batch))
                found_msgs = []
                for result_nid, result_title in results:  # Unpack the tuple here
                    checked_nid_counts[chat_id] += 1
                    if result_title:  # Only add if a title was found (i.e., NID existed)
                        found_msgs.append(f"✅ *Found*: {result_title} \\(NID: {result_nid}\\)")
                if found_msgs:
                    # Send found messages in batches to avoid very long single messages
                    await safe_send(context.bot.send_message, chat_id=chat_id, text="\n".join(found_msgs), parse_mode=constants.ParseMode.MARKDOWN_V2)
                else:
                    logger.info(f"No results found in batch starting from {i}")
                # Update progress message periodically
                # Using 100 for more frequent updates than 1000 for visibility
                if checked_nid_counts[chat_id] % 100 == 0 or checked_nid_counts[chat_id] == total:
                    try:
                        # Check if intro_msg is not None before editing
                        if intro_msg:
                            await safe_send(intro_msg.edit_text, text=f"🔄 Progress: {checked_nid_counts[chat_id]} / {total}", parse_mode=constants.ParseMode.MARKDOWN_V2)
                    except TelegramError as e:
                        logger.warning(f"Could not edit progress message: {e}")
                        # If editing fails, send a new one
                        intro_msg = await safe_send(context.bot.send_message, chat_id=chat_id, text=f"🔄 Progress: {checked_nid_counts[chat_id]} / {total}", parse_mode=constants.ParseMode.MARKDOWN_V2)
    except asyncio.CancelledError:
        # This exception is raised when task.cancel() is called
        await safe_send(context.bot.send_message, chat_id=chat_id, text="🛑 Scan truly cancelled\\.")
    except Exception as e:
        logger.error(f"Error during scan for chat {chat_id}: {e}", exc_info=True)
        await safe_send(context.bot.send_message, chat_id=chat_id, text=f"❌ An error occurred during the scan: {escape_markdown_v2(str(e))}")
    finally:
        # Clean up global state regardless of success or failure
        ongoing_searches.pop(chat_id, None)
        checked_nid_counts.pop(chat_id, None)
        total_nids_to_check.pop(chat_id, None)
        await safe_send(context.bot.send_message, chat_id=chat_id, text=f"✅ Scan finished\\! Checked {checked_nid_counts.get(chat_id, total)} NIDs\\.", parse_mode=constants.ParseMode.MARKDOWN_V2)

# Ensure final count is accurate
async def unauthorized_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for unauthorized users trying to use commands."""
    await safe_send(update.message.reply_text, "🚫 You are not authorized to use this bot\\.", parse_mode=constants.ParseMode.MARKDOWN_V2)

# === MAIN ===
def main():
    """Starts the bot."""
    # Check if the token environment variable is set
    if not TOKEN:
        logger.error("🚫 BOT_TOKEN environment variable not set. Please set it in your Railway project settings or locally.")
        return  # Exit if the token is not found
    app = Application.builder().token(TOKEN).build()
    # Define a filter for the owner's user ID
    owner_filter = filters.User(user_id=OWNER_ID)
    # Handlers for the owner (these will only respond to the OWNER_ID)
    app.add_handler(CommandHandler("start", start, filters=owner_filter))
    app.add_handler(CommandHandler("help", help_command, filters=owner_filter))
    app.add_handler(CommandHandler("search", search, filters=owner_filter))
    app.add_handler(CommandHandler("cancel", cancel, filters=owner_filter))
    app.add_handler(CommandHandler("status", status, filters=owner_filter))
    # Handler for any command (specified in the list) from non-owner users.
    # The ~ operator inverts the filter, meaning "if NOT owner_filter".
    # This handler must be added AFTER the owner_filter handlers for the same commands.
    app.add_handler(CommandHandler(
        ["start", "help", "search", "cancel", "status"],  # Commands to catch if not owner
        unauthorized_command,
        ~owner_filter  # Only trigger if the user is NOT the owner
    ))
    logger.info(f"🚀 Bot started for Owner ID: {OWNER_ID}")
    app.run_polling(drop_pending_updates=True)  # drop_pending_updates is good practice on startup

if __name__ == "__main__":
    main()
