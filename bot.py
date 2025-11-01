import aiohttp
import asyncio
import logging
import os  # Import the os module to access environment variables
from collections import defaultdict
from telegram import Update, constants, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, filters, CallbackQueryHandler
from telegram.error import RetryAfter, TelegramError

# === CONFIG ===
# Read the bot token from the environment variable 'BOT_TOKEN'
# Ensure you set this variable in your Railway project settings!
TOKEN = "8578138471:AAGvTkbbNMU2O3dGZFPEYluG4BaVoXZyUe4"
OWNER_ID = 8516723793  # Replace with your Telegram numeric user ID
API_URL = "https://learn.aakashitutor.com/api/getquizfromid?nid="
DEFAULT_BATCH_SIZE = 1000
MAX_CONCURRENT_REQUESTS = 50  # Limit concurrent API requests to prevent overwhelming the server

# === GLOBAL STATE ===
ongoing_searches = {}  # chat_id -> task
checked_nid_counts = defaultdict(int)  # chat_id -> count
total_nids_to_check = {}  # chat_id -> total
authorized_users = set()  # Set to store authorized user IDs
user_progress_messages = {}  # chat_id -> message_id

# === LOGGING ===
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# === HELPERS ===
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

async def fetch_test_data(session, nid, semaphore):
    """ Fetches test data for a given NID from the API. Returns (nid, title) if found, otherwise (nid, None). """
    async with semaphore:  # Limit concurrent requests
        try:
            async with session.get(f"{API_URL}{nid}", timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list) and data and isinstance(data[0], dict):
                        title = data[0].get("title", "No Title")
                        logger.info(f"✅ FOUND: NID {nid} - {title}")
                        return nid, title
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

# === COMMANDS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a welcome message to the user."""
    user_id = update.effective_user.id
    is_owner = user_id == OWNER_ID
    welcome_text = (
        f"👋 Welcome, {update.effective_user.first_name}!\n\n"
        "Use <code>/search &lt;start&gt; &lt;end&gt; [batch_size]</code> to begin scanning NIDs.\n\n"
        f"{'🔑 You are the bot owner.\n' if is_owner else ''}"
        "Use <code>/help</code> to see all available commands.\n\n"
        "BOT BY - kคli liຖนxx"
    )
    await safe_send(
        update.message.reply_text,
        welcome_text,
        parse_mode=constants.ParseMode.HTML
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends the help message."""
    user_id = update.effective_user.id
    is_owner = user_id == OWNER_ID
    
    help_text = (
        "📌 <b>Bot Commands</b>\n\n"
        "🔹 <b>User Commands</b>\n"
        "/start – Welcome message\n"
        "/search &lt;start&gt; &lt;end&gt; [batch_size] – Start scanning NIDs\n"
        "/cancel – Stop ongoing scan\n"
        "/status – Show scan progress and authorized users\n"
        "/help – Show this help\n"
        "/listall – List all bot commands\n\n"
    )
    
    if is_owner:
        help_text += (
            "🔹 <b>Admin Commands</b>\n"
            "/au {user_id} – Authorize a user\n"
            "/ru {user_id} – Revoke user authorization\n"
            "/list – List all authorized users\n\n"
        )
    
    help_text += "BOT BY - kคli liຖนxx"
    
    await safe_send(
        update.message.reply_text,
        help_text,
        parse_mode=constants.ParseMode.HTML
    )

async def listall_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists all bot commands."""
    user_id = update.effective_user.id
    is_owner = user_id == OWNER_ID
    
    listall_text = (
        "📋 <b>All Bot Commands</b>\n\n"
        "🔹 <b>User Commands</b>\n"
        "/start – Welcome message\n"
        "/search &lt;start&gt; &lt;end&gt; [batch_size] – Start scanning NIDs\n"
        "/cancel – Stop ongoing scan\n"
        "/status – Show scan progress and authorized users\n"
        "/help – Show help message\n"
        "/listall – List all bot commands\n\n"
    )
    
    if is_owner:
        listall_text += (
            "🔹 <b>Admin Commands</b>\n"
            "/au {user_id} – Authorize a user\n"
            "/ru {user_id} – Revoke user authorization\n"
            "/list – List all authorized users\n\n"
        )
    
    listall_text += "BOT BY - kคli liຖนxx"
    
    await safe_send(
        update.message.reply_text,
        listall_text,
        parse_mode=constants.ParseMode.HTML
    )

async def admin_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows all admin commands."""
    admin_text = (
        "🔧 <b>Admin Commands</b>\n\n"
        "/au {user_id} – Authorize a user to use the bot\n"
        "/ru {user_id} – Revoke user authorization\n"
        "/list – List all authorized users\n"
        "/search &lt;start&gt; &lt;end&gt; [batch_size] – Start scanning NIDs\n"
        "/cancel – Stop ongoing scan\n"
        "/status – Show scan progress and authorized users\n"
        "/admin – Show this admin command list\n"
        "/help – Show the general help message\n"
        "/listall – List all bot commands\n\n"
        "BOT BY - kคli liຖนxx"
    )
    await safe_send(
        update.message.reply_text,
        admin_text,
        parse_mode=constants.ParseMode.HTML
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the current scan progress and authorized users."""
    chat_id = update.effective_chat.id
    current = checked_nid_counts.get(chat_id, 0)
    total = total_nids_to_check.get(chat_id, '?')
    user_id = update.effective_user.id
    is_owner = user_id == OWNER_ID
    
    if chat_id in ongoing_searches and not ongoing_searches[chat_id].done():
        progress_percent = round((current / total) * 100, 1) if total != '?' else 0
        progress_bar = "█" * int(progress_percent / 5) + "░" * (20 - int(progress_percent / 5))
        
        status_text = (
            f"🔄 <b>Current Scan Status</b>\n\n"
            f"{progress_bar} {progress_percent}%\n"
            f"📈 <b>Checked</b>: {current} / {total}\n"
            f"🏃 <b>Status</b>: Running\n\n"
        )
    else:
        status_text = (
            f"ℹ️ <b>No active scan running</b>\n\n"
        )
    
    # Add authorized users information
    if is_owner:
        status_text += (
            f"🔑 <b>Authorized Users</b>\n"
            f"• Owner: <code>{OWNER_ID}</code>\n"
        )
        
        if authorized_users:
            for user in sorted(authorized_users):
                status_text += f"• <code>{user}</code>\n"
        else:
            status_text += "• No additional authorized users\n"
    
    status_text += "\nBOT BY - kคli liຖนxx"
    
    await safe_send(
        update.message.reply_text,
        status_text,
        parse_mode=constants.ParseMode.HTML
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels an ongoing scan."""
    chat_id = update.effective_chat.id
    task = ongoing_searches.get(chat_id)
    
    if task and not task.done():
        task.cancel()
        # Clean up user data
        ongoing_searches.pop(chat_id, None)
        checked_nid_counts.pop(chat_id, None)
        total_nids_to_check.pop(chat_id, None)
        user_progress_messages.pop(chat_id, None)
        
        await safe_send(
            update.message.reply_text,
            "🛑 Scan cancelled.\n\nBOT BY - kคli liຖนxx",
            parse_mode=constants.ParseMode.HTML
        )
    else:
        await safe_send(
            update.message.reply_text,
            "ℹ️ No active scan to cancel.\n\nBOT BY - kคli liຖนxx",
            parse_mode=constants.ParseMode.HTML
        )

async def authorize_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Authorizes a user to use the bot."""
    if len(context.args) < 1:
        await safe_send(
            update.message.reply_text,
            "❗ Usage: /au <user_id>\n\nBOT BY - kคli liຖนxx",
            parse_mode=constants.ParseMode.HTML
        )
        return
    
    try:
        user_id = int(context.args[0])
        authorized_users.add(user_id)
        await safe_send(
            update.message.reply_text,
            f"✅ User {user_id} has been authorized to use the bot.\n\nBOT BY - kคli liຖนxx",
            parse_mode=constants.ParseMode.HTML
        )
        logger.info(f"User {user_id} authorized by owner {update.effective_user.id}")
    except ValueError:
        await safe_send(
            update.message.reply_text,
            "❗ Invalid user ID. Please use a numeric ID.\n\nBOT BY - kคli liຖนxx",
            parse_mode=constants.ParseMode.HTML
        )

async def revoke_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Revokes authorization for a user."""
    if len(context.args) < 1:
        await safe_send(
            update.message.reply_text,
            "❗ Usage: /ru <user_id>\n\nBOT BY - kคli liຖนxx",
            parse_mode=constants.ParseMode.HTML
        )
        return
    
    try:
        user_id = int(context.args[0])
        if user_id in authorized_users:
            authorized_users.remove(user_id)
            await safe_send(
                update.message.reply_text,
                f"🚫 User {user_id} authorization has been revoked.\n\nBOT BY - kคli liຖนxx",
                parse_mode=constants.ParseMode.HTML
            )
            logger.info(f"User {user_id} authorization revoked by owner {update.effective_user.id}")
        else:
            await safe_send(
                update.message.reply_text,
                f"⚠️ User {user_id} was not in the authorized list.\n\nBOT BY - kคli liຖนxx",
                parse_mode=constants.ParseMode.HTML
            )
    except ValueError:
        await safe_send(
            update.message.reply_text,
            "❗ Invalid user ID. Please use a numeric ID.\n\nBOT BY - kคli liຖนxx",
            parse_mode=constants.ParseMode.HTML
        )

async def list_authorized(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists all authorized users."""
    if not authorized_users:
        await safe_send(
            update.message.reply_text,
            "📋 No additional users are authorized.\n\nBOT BY - kคli liຖนxx",
            parse_mode=constants.ParseMode.HTML
        )
        return
    
    user_list = "\n".join([f"• <code>{user_id}</code>" for user_id in sorted(authorized_users)])
    await safe_send(
        update.message.reply_text,
        f"📋 Authorized users:\n{user_list}\n\nBOT BY - kคli liຖนxx",
        parse_mode=constants.ParseMode.HTML
    )

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts scanning NIDs within a specified range."""
    chat_id = update.effective_chat.id
    args = context.args
    
    if len(args) < 2:
        await safe_send(
            update.message.reply_text,
            "❗ Usage: /search <start_nid> <end_nid> [batch_size]\n\nBOT BY - kคli liຖนxx",
            parse_mode=constants.ParseMode.HTML
        )
        return
    
    try:
        start_nid = int(args[0])
        end_nid = int(args[1])
        batch_size = int(args[2]) if len(args) > 2 else DEFAULT_BATCH_SIZE
        
        if start_nid <= 0 or end_nid <= 0:
            await safe_send(
                update.message.reply_text,
                "⚠️ NID values must be positive integers.\n\nBOT BY - kคli liຖนxx",
                parse_mode=constants.ParseMode.HTML
            )
            return
        
        if start_nid > end_nid:
            await safe_send(
                update.message.reply_text,
                "⚠️ Start NID must be less than or equal to End NID.\n\nBOT BY - kคli liຖนxx",
                parse_mode=constants.ParseMode.HTML
            )
            return
        
        # Check if a search is already ongoing for this chat
        if chat_id in ongoing_searches and not ongoing_searches[chat_id].done():
            await safe_send(
                update.message.reply_text,
                "⏳ A scan is already running. Use /cancel to stop it.\n\nBOT BY - kคli liຖนxx",
                parse_mode=constants.ParseMode.HTML
            )
            return
        
        # Initialize user-specific data
        checked_nid_counts[chat_id] = 0
        total_nids_to_check[chat_id] = end_nid - start_nid + 1
        
        # Create and start the search task in the background
        task = asyncio.create_task(
            perform_search(chat_id, start_nid, end_nid, batch_size, context)
        )
        ongoing_searches[chat_id] = task
        
        # Don't wait for the task to complete, let it run in the background
        # This ensures the bot remains responsive to other commands
        
    except ValueError:
        await safe_send(
            update.message.reply_text,
            "❗ Invalid NID or batch size. Please use integers.\n\nBOT BY - kคli liຖนxx",
            parse_mode=constants.ParseMode.HTML
        )

# === SEARCH TASK ===
async def perform_search(chat_id, start_nid, end_nid, batch_size, context):
    """Performs the asynchronous NID scanning in the background."""
    total = end_nid - start_nid + 1
    
    # Send a typing action to indicate the bot is working
    await safe_send(
        context.bot.send_chat_action,
        chat_id=chat_id,
        action=constants.ChatAction.TYPING
    )
    
    # Create a fancy intro message with an inline keyboard
    keyboard = [
        [InlineKeyboardButton("📊 View Progress", callback_data=f"progress_{chat_id}")],
        [InlineKeyboardButton("🛑 Cancel Scan", callback_data=f"cancel_{chat_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    intro_msg = await safe_send(
        context.bot.send_message,
        chat_id=chat_id,
        text=f"🔍 <b>Scan Initiated</b>\n\n📚 <b>Scanning Range</b>: {start_nid} to {end_nid}\n📊 <b>Total NIDs</b>: {total}\n📦 <b>Batch Size</b>: {batch_size}\n\n⏳ <b>Status</b>: Initializing...\n\nBOT BY - kคli liຖนxx",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=reply_markup
    )
    
    if intro_msg:
        user_progress_messages[chat_id] = intro_msg.message_id
    
    # Create a semaphore to limit concurrent requests
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    
    try:
        async with aiohttp.ClientSession() as session:
            for i in range(start_nid, end_nid + 1, batch_size):
                # Check for cancellation before processing each batch
                if chat_id not in ongoing_searches or ongoing_searches[chat_id].cancelled():
                    await safe_send(
                        context.bot.send_message,
                        chat_id=chat_id,
                        text="🛑 Scan cancelled by user.\n\nBOT BY - kคli liຖนxx",
                        parse_mode=constants.ParseMode.HTML
                    )
                    return
                
                batch = range(i, min(i + batch_size, end_nid + 1))
                
                # Use asyncio.gather for concurrent fetching of NIDs in the batch
                # Pass the semaphore to each fetch_test_data call to limit concurrent requests
                results = await asyncio.gather(
                    *(fetch_test_data(session, nid, semaphore) for nid in batch),
                    return_exceptions=True  # Don't let one failure stop the entire batch
                )
                
                found_items = []
                for result in results:
                    # Handle exceptions from individual requests
                    if isinstance(result, Exception):
                        logger.error(f"Error in batch processing: {result}")
                        continue
                    
                    result_nid, result_title = result
                    checked_nid_counts[chat_id] += 1
                    
                    if result_title:  # Only add if a title was found (i.e., NID existed)
                        found_items.append((result_nid, result_title))
                
                # If we found any items, send them with enhanced formatting
                if found_items:
                    # Create a message for each found item with enhanced UI
                    for nid, title in found_items:
                        item_msg = (
                            f"🎯 <b>NID Found</b>\n\n"
                            f"📝 <b>Title</b>: {title}\n"
                            f"🆔 <b>ID</b>: <code>{nid}</code>\n\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n\n"
                            f"BOT BY - kคli liຖนxx"
                        )
                        await safe_send(
                            context.bot.send_message,
                            chat_id=chat_id,
                            text=item_msg,
                            parse_mode=constants.ParseMode.HTML
                        )
                else:
                    logger.info(f"No results found in batch starting from {i}")
                
                # Update progress message periodically
                # Using 100 for more frequent updates than 1000 for visibility
                if checked_nid_counts[chat_id] % 100 == 0 or checked_nid_counts[chat_id] == total:
                    progress_percent = round((checked_nid_counts[chat_id] / total) * 100, 1)
                    progress_bar = "█" * int(progress_percent / 5) + "░" * (20 - int(progress_percent / 5))
                    
                    # Update the keyboard with current progress
                    keyboard = [
                        [InlineKeyboardButton(f"📊 Progress: {progress_percent}%", callback_data=f"progress_{chat_id}")],
                        [InlineKeyboardButton("🛑 Cancel Scan", callback_data=f"cancel_{chat_id}")]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    try:
                        # Try to edit the existing progress message
                        if chat_id in user_progress_messages:
                            await safe_send(
                                context.bot.edit_message_text,
                                chat_id=chat_id,
                                message_id=user_progress_messages[chat_id],
                                text=f"🔍 <b>Scan In Progress</b>\n\n"
                                     f"📚 <b>Scanning Range</b>: {start_nid} to {end_nid}\n"
                                     f"📊 <b>Progress</b>: {progress_bar} {progress_percent}%\n"
                                     f"📈 <b>Checked</b>: {checked_nid_counts[chat_id]} / {total}\n"
                                     f"📦 <b>Batch Size</b>: {batch_size}\n\n"
                                     f"⏳ <b>Status</b>: Scanning...\n\n"
                                     f"BOT BY - kคli liຖนxx",
                                parse_mode=constants.ParseMode.HTML,
                                reply_markup=reply_markup
                            )
                    except Exception as e:
                        logger.warning(f"Could not edit progress message: {e}")
                        # If editing fails, send a new one
                        new_msg = await safe_send(
                            context.bot.send_message,
                            chat_id=chat_id,
                            text=f"🔍 <b>Scan In Progress</b>\n\n"
                                 f"📚 <b>Scanning Range</b>: {start_nid} to {end_nid}\n"
                                 f"📊 <b>Progress</b>: {progress_bar} {progress_percent}%\n"
                                 f"📈 <b>Checked</b>: {checked_nid_counts[chat_id]} / {total}\n"
                                 f"📦 <b>Batch Size</b>: {batch_size}\n\n"
                                 f"⏳ <b>Status</b>: Scanning...\n\n"
                                 f"BOT BY - kคli liຖนxx",
                            parse_mode=constants.ParseMode.HTML,
                            reply_markup=reply_markup
                        )
                        if new_msg:
                            user_progress_messages[chat_id] = new_msg.message_id
                
                # Small delay to prevent overwhelming the API
                await asyncio.sleep(0.1)
                
    except asyncio.CancelledError:
        # This exception is raised when task.cancel() is called
        await safe_send(
            context.bot.send_message,
            chat_id=chat_id,
            text="🛑 Scan truly cancelled.\n\nBOT BY - kคli liຖนxx",
            parse_mode=constants.ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error during scan for chat {chat_id}: {e}", exc_info=True)
        await safe_send(
            context.bot.send_message,
            chat_id=chat_id,
            text=f"❌ An error occurred during the scan: {str(e)}\n\nBOT BY - kคli liຖนxx",
            parse_mode=constants.ParseMode.HTML
        )
    finally:
        # Clean up user-specific data regardless of success or failure
        ongoing_searches.pop(chat_id, None)
        checked_nid_counts.pop(chat_id, None)
        total_nids_to_check.pop(chat_id, None)
        user_progress_messages.pop(chat_id, None)
        
        # Send a completion message with summary
        final_count = checked_nid_counts.get(chat_id, total)
        await safe_send(
            context.bot.send_message,
            chat_id=chat_id,
            text=f"✅ <b>Scan Complete</b>\n\n"
                 f"📊 <b>Summary</b>\n"
                 f"📚 <b>Range</b>: {start_nid} to {end_nid}\n"
                 f"📈 <b>Checked</b>: {final_count} NIDs\n"
                 f"🏁 <b>Status</b>: Completed\n\n"
                 f"Thank you for using the NID Scanner Bot!\n\n"
                 f"BOT BY - kคli liຖนxx",
            parse_mode=constants.ParseMode.HTML
        )

# Callback handler for inline buttons
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles button callbacks from inline keyboards."""
    query = update.callback_query
    await query.answer()
    
    chat_id = update.effective_chat.id
    data = query.data
    
    if data.startswith("progress_"):
        # Show current progress
        current = checked_nid_counts.get(chat_id, 0)
        total = total_nids_to_check.get(chat_id, '?')
        progress_percent = round((current / total) * 100, 1) if total != '?' else 0
        progress_bar = "█" * int(progress_percent / 5) + "░" * (20 - int(progress_percent / 5))
        
        await safe_send(
            context.bot.send_message,
            chat_id=chat_id,
            text=f"📊 <b>Current Progress</b>\n\n"
                 f"{progress_bar} {progress_percent}%\n"
                 f"📈 <b>Checked</b>: {current} / {total}\n\n"
                 f"BOT BY - kคli liຖนxx",
            parse_mode=constants.ParseMode.HTML
        )
    elif data.startswith("cancel_"):
        # Cancel the scan
        task = ongoing_searches.get(chat_id)
        if task and not task.done():
            task.cancel()
            await safe_send(
                context.bot.send_message,
                chat_id=chat_id,
                text="🛑 Scan cancellation requested.\n\nBOT BY - kคli liຖนxx",
                parse_mode=constants.ParseMode.HTML
            )
        else:
            await safe_send(
                context.bot.send_message,
                chat_id=chat_id,
                text="ℹ️ No active scan to cancel.\n\nBOT BY - kคli liຖนxx",
                parse_mode=constants.ParseMode.HTML
            )

async def unauthorized_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for unauthorized users trying to use commands."""
    await safe_send(
        update.message.reply_text,
        "🚫 You are not authorized to use this bot.\n"
        "Please contact the bot owner for access.\n\n"
        "BOT BY - kคli liຖนxx",
        parse_mode=constants.ParseMode.HTML
    )

# === AUTHORIZATION MIDDLEWARE ===
def is_authorized(user_id: int) -> bool:
    """Check if a user is authorized to use the bot."""
    return user_id == OWNER_ID or user_id in authorized_users

# === MAIN ===
def main():
    """Starts the bot."""
    # Check if the token environment variable is set
    if not TOKEN:
        logger.error("🚫 BOT_TOKEN environment variable not set. Please set it in your Railway project settings or locally.")
        return  # Exit if the token is not found
    
    # Create the Application with increased concurrent updates
    app = Application.builder().token(TOKEN).concurrent_updates(True).build()
    
    # Create a custom filter class for authorized users
    class AuthorizedFilter(filters.BaseFilter):
        def filter(self, update):
            return is_authorized(update.effective_user.id)
    
    authorized_filter = AuthorizedFilter()
    
    # Handlers for authorized users (owner + explicitly authorized users)
    app.add_handler(CommandHandler("start", start, filters=authorized_filter))
    app.add_handler(CommandHandler("help", help_command, filters=authorized_filter))
    app.add_handler(CommandHandler("listall", listall_command, filters=authorized_filter))
    app.add_handler(CommandHandler("admin", admin_commands, filters=authorized_filter))
    app.add_handler(CommandHandler("search", search, filters=authorized_filter))
    app.add_handler(CommandHandler("cancel", cancel, filters=authorized_filter))
    app.add_handler(CommandHandler("status", status, filters=authorized_filter))
    app.add_handler(CommandHandler("au", authorize_user, filters=authorized_filter))
    app.add_handler(CommandHandler("ru", revoke_user, filters=authorized_filter))
    app.add_handler(CommandHandler("list", list_authorized, filters=authorized_filter))
    
    # Handler for button callbacks
    app.add_handler(CallbackQueryHandler(button_callback))
    
    # Handler for any command from unauthorized users
    app.add_handler(CommandHandler(
        ["start", "help", "listall", "admin", "search", "cancel", "status", "au", "ru", "list"],
        unauthorized_command
    ))
    
    logger.info(f"🚀 Bot started for Owner ID: {OWNER_ID}")
    app.run_polling(drop_pending_updates=True)  # drop_pending_updates is good practice on startup

if __name__ == "__main__":
    main()
