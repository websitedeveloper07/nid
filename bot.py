import aiohttp
import asyncio
import os
import logging
from telegram import Update, constants
from telegram.ext import Application, CommandHandler, ContextTypes
from collections import defaultdict
import re # Import re for regular expressions

# === CONFIG ===
# You MUST replace "YOUR_BOT_TOKEN_HERE" with your actual Telegram Bot Token
TOKEN = "7622336683:AAFBxrx1hPuG_5ZNY14zQjrxzRgPaS_Jf5A"
API_URL = "https://learn.aakashitutor.com/api/getquizfromid?nid="
DEFAULT_BATCH_SIZE = 500 # Default safe value
# Removed MAX_CONCURRENT_SEARCHES as it's handled implicitly by the per-chat logic.

# === LOGGING ===
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# === GLOBAL STATE ===
# Dictionary to store ongoing search tasks for each chat
ongoing_searches = {}
# Dictionary to store checked NID counts for each chat
checked_nid_counts = defaultdict(int)
# Dictionary to store total NIDs to check for each chat
total_nids_to_check = {}

# === Helper Functions ===

def escape_markdown_v2(text: str) -> str:
    """Helper function to escape special characters for MarkdownV2."""
    # List of special characters that need to be escaped in MarkdownV2
    # Reference: https://core.telegram.org/bots/api#markdown-style
    special_chars = r'_*[]()~`>#+-=|{}.!'
    # Escape each special character with a backslash
    return re.sub(f"([{re.escape(special_chars)}])", r"\\\1", text)

async def fetch_test_data(session, nid):
    """
    Fetches test metadata for a given NID.
    Returns (nid, title) if found, otherwise (nid, None).
    """
    try:
        async with session.get(f"{API_URL}{nid}", timeout=15) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, list) and data:
                    title = data[0].get("title", "No Title")
                    # Escape title for MarkdownV2 before returning
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
    """
    Performs the NID search within the specified range.
    This function runs as a separate task.
    """
    message = None
    total_nids = end_nid - start_nid + 1
    total_nids_to_check[chat_id] = total_nids # Store total for updates

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.TYPING)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text=f"🔍 Starting NID search from `{start_nid}` to `{end_nid}`\. Total NIDs to check: `{total_nids}`\.\n"
                 f"Progress: `0` / `{total_nids}` completed\.",
            parse_mode=constants.ParseMode.MARKDOWN_V2 # Use MarkdownV2 for better escaping control
        )

        async with aiohttp.ClientSession() as session:
            for i in range(start_nid, end_nid + 1, batch_size):
                # Check if the search has been cancelled
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
                        continue # Skip network errors or timeouts for individual NIDs

                    nid, title = result # Title is already escaped here
                    checked_nid_counts[chat_id] += 1

                    if title:
                        valid_nids_found_in_batch.append(f"✅ Found: {title} \(NID: `{nid}`\)")
                        logger.info(f"FOUND: NID {nid} - {title}")

                if valid_nids_found_in_batch:
                    response_text = "\n".join(valid_nids_found_in_batch)
                    # Send valid NIDs, ensuring MarkdownV2 is used
                    await context.bot.send_message(chat_id=chat_id, text=response_text, parse_mode=constants.ParseMode.MARKDOWN_V2)

                # Update status periodically (e.g., every 500 NIDs or if batch is processed)
                if checked_nid_counts[chat_id] % 500 == 0 or (batch_end == end_nid) :
                    if message:
                        current_checked = checked_nid_counts[chat_id]
                        total_nids_val = total_nids_to_check.get(chat_id, total_nids) # Use stored total for consistency
                        
                        await message.edit_text(
                            f"🔍 Searching NIDs from `{start_nid}` to `{end_nid}`\.\n"
                            f"Progress: `{current_checked}` / `{total_nids_val}` completed\.",
                            parse_mode=constants.ParseMode.MARKDOWN_V2
                        )
                    else: # Fallback if message somehow wasn't sent initially (shouldn't happen)
                        message = await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"🔍 Searching NIDs from `{start_nid}` to `{end_nid}`\.\n"
                                 f"Progress: `{checked_nid_counts[chat_id]}` / `{total_nids}` completed\.",
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
        # Clean up the ongoing search task and count
        if chat_id in ongoing_searches:
            del ongoing_searches[chat_id]
        if chat_id in checked_nid_counts:
            del checked_nid_counts[chat_id]
        if chat_id in total_nids_to_check:
            del total_nids_to_check[chat_id]

        # Finalize the status message if it exists and hasn't been replaced by a completion/cancellation message
        if message:
            try:
                # Check if the message text still contains "Progress:" to avoid editing a "complete" or "cancelled" message
                if "Progress:" in message.text:
                    final_checked = checked_nid_counts.get(chat_id, 0) # Get 0 if already deleted
                    final_total = total_nids_to_check.get(chat_id, total_nids) # Get default if deleted
                    await message.edit_text(
                        f"✅ Search session ended\. Total NIDs checked: `{final_checked}` / `{final_total}`\.",
                        parse_mode=constants.ParseMode.MARKDOWN_V2
                    )
            except Exception as e:
                logger.warning(f"Could not edit final status message for chat {chat_id}: {e}")


# === Telegram Command Handlers ===

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a welcome message and explains available commands."""
    await update.message.reply_text(
        "👋 Welcome\! I can help you search for NIDs on Aakash iTutor\.\n\n"
        "Here are the commands you can use:\n"
        "• `/search <start_nid> <end_nid>`: Search for NIDs within a specified range\. "
        "Example: `/search 4379492956 4379493000`\n"
        "• `/cancel`: Stop any ongoing NID search\.\n"
        "• `/status`: Get the current status of your ongoing search, if any\.\n"
        "• `/help`: Show this help message again\.",
        parse_mode=constants.ParseMode.MARKDOWN_V2
    )

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles the /search command.
    Usage: /search <start_nid> <end_nid> [batch_size]
    """
    chat_id = update.effective_chat.id

    if chat_id in ongoing_searches and not ongoing_searches[chat_id].done():
        await update.message.reply_text("⏳ You already have an active search running\. Please `/cancel` it first if you want to start a new one\.", parse_mode=constants.ParseMode.MARKDOWN_V2)
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: `/search <start_nid> <end_nid> [batch_size]`\n"
            "Example: `/search 4379492956 4379493000`\n"
            "The `batch_size` is optional and defaults to 2000\.",
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )
        return

    try:
        start_nid = int(args[0])
        end_nid = int(args[1])
        batch_size = DEFAULT_BATCH_SIZE
        if len(args) > 2:
            batch_size = int(args[2])
            if not (1 <= batch_size <= 10000): # Reasonable limits for batch size
                await update.message.reply_text("Batch size must be between 1 and 10000\.", parse_mode=constants.ParseMode.MARKDOWN_V2)
                return

        if not (1000000000 <= start_nid <= 9999999999) or not (1000000000 <= end_nid <= 9999999999):
            await update.message.reply_text("NID values must be 10 digits\.", parse_mode=constants.ParseMode.MARKDOWN_V2)
            return

        if start_nid > end_nid:
            await update.message.reply_text("`start_nid` cannot be greater than `end_nid`\.", parse_mode=constants.ParseMode.MARKDOWN_V2)
            return

        # Calculate total NIDs for range limit check
        total_range_nids = end_nid - start_nid + 1
        if total_range_nids > 500000000: # Example limit for range to prevent extremely long scans
            await update.message.reply_text(
                "The requested NID range is too large\. Please specify a range of maximum 500,00000 NIDs at a time\.",
                parse_mode=constants.ParseMode.MARKDOWN_V2
            )
            return

        # Start the search as a non-blocking task
        checked_nid_counts[chat_id] = 0 # Reset count for new search
        total_nids_to_check[chat_id] = total_range_nids # Store total for consistent updates
        task = asyncio.create_task(perform_search(chat_id, start_nid, end_nid, batch_size, context))
        ongoing_searches[chat_id] = task

    except ValueError:
        await update.message.reply_text("Please provide valid numerical NID values and an optional batch size\.", parse_mode=constants.ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Error in search_command for chat {chat_id}: {e}", exc_info=True)
        await update.message.reply_text(f"An unexpected error occurred: `{escape_markdown_v2(str(e))}`", parse_mode=constants.ParseMode.MARKDOWN_V2)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels an ongoing NID search for the current chat."""
    chat_id = update.effective_chat.id

    if chat_id in ongoing_searches and not ongoing_searches[chat_id].done():
        ongoing_searches[chat_id].cancel()
        await update.message.reply_text("Cancelling your ongoing NID search\. Please wait a moment\.\.\.", parse_mode=constants.ParseMode.MARKDOWN_V2)
    else:
        await update.message.reply_text("You don't have an active NID search to cancel\.", parse_mode=constants.ParseMode.MARKDOWN_V2)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Provides the status of the ongoing NID search."""
    chat_id = update.effective_chat.id

    if chat_id in ongoing_searches and not ongoing_searches[chat_id].done():
        checked_count = checked_nid_counts.get(chat_id, 0)
        total_count = total_nids_to_check.get(chat_id, "N/A") # Get total, or "N/A" if not set
        await update.message.reply_text(f"🔍 An NID search is currently active\. Checked `{checked_count}` / `{total_count}` NIDs so far\.", parse_mode=constants.ParseMode.MARKDOWN_V2)
    else:
        await update.message.reply_text("No active NID search found for your chat\.", parse_mode=constants.ParseMode.MARKDOWN_V2)

# === Main function ===
def main():
    """Starts the bot."""
    application = Application.builder().token(TOKEN).build()

    # Register command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", start_command)) # /help also shows start message
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("status", status_command))

    logger.info("🚀 Bot is starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
