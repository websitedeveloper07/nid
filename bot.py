import aiohttp
import asyncio
import logging
import os
from collections import defaultdict
from telegram import Update, constants, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, filters, CallbackQueryHandler
from telegram.error import RetryAfter, TelegramError

# === CONFIG ===
TOKEN = "8578138471:AAGvTkbbNMU2O3dGZFPEYluG4BaVoXZyUe4"
OWNER_ID = 8516723793
API_URL = "https://learn.aakashitutor.com/api/getquizfromid?nid="
DEFAULT_BATCH_SIZE = 1000
MAX_CONCURRENT_REQUESTS = 50

# === GLOBAL STATE ===
ongoing_searches = {}
checked_nid_counts = defaultdict(int)
total_nids_to_check = {}
authorized_users = set()
user_progress_messages = {}

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

def is_authorized(user_id: int) -> bool:
    """Check if a user is authorized to use the bot."""
    return user_id == OWNER_ID or user_id in authorized_users

async def fetch_test_data(session, nid, semaphore):
    """ Fetches test data for a given NID from the API. Returns (nid, title) if found, otherwise (nid, None). """
    async with semaphore:
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

# === AUTHORIZATION MIDDLEWARE ===
def authorized_command_handler(func):
    """Decorator to check if user is authorized before executing command."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update.effective_user.id):
            await unauthorized_command(update, context)
            return
        return await func(update, context)
    return wrapper

# === COMMANDS ===
@authorized_command_handler
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a welcome message to the user."""
    user_id = update.effective_user.id
    is_owner = user_id == OWNER_ID
    
    # Create a beautiful welcome message with inline keyboard
    keyboard = [
        [InlineKeyboardButton("📖 Help", callback_data="help")],
        [InlineKeyboardButton("🔍 Start Scan", callback_data="search_prompt")],
        [InlineKeyboardButton("📊 Status", callback_data="status")],
    ]
    
    if is_owner:
        keyboard.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_text = (
        f"🌟 <b>Welcome to NID Scanner Bot</b> 🌟\n\n"
        f"👋 Hello, <b>{update.effective_user.first_name}</b>!\n\n"
        f"🔍 <b>What I can do:</b>\n"
        f"• Scan NID ranges for valid tests\n"
        f"• Real-time progress tracking\n"
        f"• Batch processing for efficiency\n\n"
        f"{'👑 <b>Admin Status:</b> Owner\n' if is_owner else '✅ <b>Status:</b> Authorized User\n'}"
        f"🚀 <b>Ready to scan!</b> Choose an option below:\n\n"
        f"<i>BOT BY - kคli liຖนxx</i>"
    )
    
    await safe_send(
        update.message.reply_text,
        welcome_text,
        parse_mode=constants.ParseMode.HTML,
        reply_markup=reply_markup
    )

@authorized_command_handler
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends the help message."""
    user_id = update.effective_user.id
    is_owner = user_id == OWNER_ID
    
    help_text = (
        "📚 <b>NID Scanner Bot - Help Guide</b> 📚\n\n"
        "🔹 <b>User Commands:</b>\n"
        "• <code>/start</code> - Show welcome menu\n"
        "• <code>/search &lt;start&gt; &lt;end&gt; [batch]</code> - Start scanning\n"
        "• <code>/cancel</code> - Cancel current scan\n"
        "• <code>/status</code> - View scan progress\n"
        "• <code>/help</code> - Show this help\n"
        "• <code>/listall</code> - List all commands\n\n"
    )
    
    if is_owner:
        help_text += (
            "👑 <b>Admin Commands:</b>\n"
            "• <code>/au &lt;user_id&gt;</code> - Authorize user\n"
            "• <code>/ru &lt;user_id&gt;</code> - Revoke authorization\n"
            "• <code>/list</code> - List authorized users\n"
            "• <code>/admin</code> - Show admin panel\n\n"
        )
    
    help_text += (
        "💡 <b>Tips:</b>\n"
        "• Use batch size 500-1000 for optimal performance\n"
        "• Monitor progress with inline buttons\n"
        "• Cancel anytime with /cancel\n\n"
        "<i>BOT BY - kคli liຖนxx</i>"
    )
    
    await safe_send(
        update.message.reply_text,
        help_text,
        parse_mode=constants.ParseMode.HTML
    )

@authorized_command_handler
async def listall_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists all bot commands with detailed descriptions."""
    user_id = update.effective_user.id
    is_owner = user_id == OWNER_ID
    
    listall_text = (
        "📋 <b>Complete Command List</b> 📋\n\n"
        "🔹 <b>Basic Commands:</b>\n"
        "• <code>/start</code> - Display welcome menu with options\n"
        "• <code>/help</code> - Show help guide and tips\n"
        "• <code>/listall</code> - Show this complete command list\n\n"
        
        "🔹 <b>Scanning Commands:</b>\n"
        "• <code>/search &lt;start&gt; &lt;end&gt; [batch]</code> - Begin NID scan\n"
        "  • start: Starting NID number\n"
        "  • end: Ending NID number\n"
        "  • batch: Optional batch size (default: 1000)\n"
        "• <code>/cancel</code> - Stop current scanning process\n"
        "• <code>/status</code> - Show current scan progress\n\n"
    )
    
    if is_owner:
        listall_text += (
            "👑 <b>Admin Commands:</b>\n"
            "• <code>/au &lt;user_id&gt;</code> - Add user to authorized list\n"
            "• <code>/ru &lt;user_id&gt;</code> - Remove user from authorized list\n"
            "• <code>/list</code> - Display all authorized users\n"
            "• <code>/admin</code> - Show admin control panel\n\n"
        )
    
    listall_text += (
        "⚡ <b>Features:</b>\n"
        "• Real-time progress tracking\n"
        "• Concurrent request processing\n"
        "• Automatic error recovery\n"
        "• Flood control protection\n\n"
        "<i>BOT BY - kคli liຖนxx</i>"
    )
    
    await safe_send(
        update.message.reply_text,
        listall_text,
        parse_mode=constants.ParseMode.HTML
    )

@authorized_command_handler
async def admin_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows admin panel with options."""
    keyboard = [
        [InlineKeyboardButton("👥 Authorized Users", callback_data="list_users")],
        [InlineKeyboardButton("➕ Authorize User", callback_data="authorize_prompt")],
        [InlineKeyboardButton("➖ Revoke User", callback_data="revoke_prompt")],
        [InlineKeyboardButton("📊 Bot Statistics", callback_data="bot_stats")],
        [InlineKeyboardButton("🔙 Back to Main", callback_data="start")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    admin_text = (
        "⚙️ <b>Admin Control Panel</b> ⚙️\n\n"
        f"👑 <b>Owner ID:</b> <code>{OWNER_ID}</code>\n"
        f"👥 <b>Authorized Users:</b> {len(authorized_users)}\n"
        f"🔍 <b>Active Scans:</b> {len(ongoing_searches)}\n\n"
        "Select an option below:\n\n"
        "<i>BOT BY - kคli liຖนxx</i>"
    )
    
    await safe_send(
        update.message.reply_text,
        admin_text,
        parse_mode=constants.ParseMode.HTML,
        reply_markup=reply_markup
    )

@authorized_command_handler
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the current scan progress and system status."""
    chat_id = update.effective_chat.id
    current = checked_nid_counts.get(chat_id, 0)
    total = total_nids_to_check.get(chat_id, '?')
    user_id = update.effective_user.id
    is_owner = user_id == OWNER_ID
    
    # Create status keyboard
    keyboard = [
        [InlineKeyboardButton("🔄 Refresh Status", callback_data="status")],
        [InlineKeyboardButton("🔍 Start New Scan", callback_data="search_prompt")],
    ]
    
    if chat_id in ongoing_searches and not ongoing_searches[chat_id].done():
        keyboard.append([InlineKeyboardButton("🛑 Cancel Scan", callback_data=f"cancel_{chat_id}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if chat_id in ongoing_searches and not ongoing_searches[chat_id].done():
        progress_percent = round((current / total) * 100, 1) if total != '?' else 0
        progress_bar = "█" * int(progress_percent / 5) + "░" * (20 - int(progress_percent / 5))
        
        status_text = (
            f"🔄 <b>Scan Status</b>\n\n"
            f"{progress_bar} {progress_percent}%\n"
            f"📈 <b>Progress:</b> {current} / {total}\n"
            f"🏃 <b>Status:</b> <code>Running</code>\n"
            f"⚡ <b>Performance:</b> {MAX_CONCURRENT_REQUESTS} concurrent requests\n\n"
        )
    else:
        status_text = (
            f"ℹ️ <b>System Status</b>\n\n"
            f"🏃 <b>Scan Status:</b> <code>Idle</code>\n"
            f"📊 <b>Last Checked:</b> {current} NIDs\n"
            f"⚡ <b>Ready for new scan</b>\n\n"
        )
    
    # Add authorized users information for owner
    if is_owner:
        status_text += (
            f"🔑 <b>Authorization Info</b>\n"
            f"• <b>Owner:</b> <code>{OWNER_ID}</code>\n"
            f"• <b>Authorized Users:</b> {len(authorized_users)}\n"
        )
        
        if authorized_users:
            status_text += f"• <b>Users:</b> <code>{', '.join(map(str, sorted(authorized_users)))}</code>\n"
        else:
            status_text += "• <b>Users:</b> <code>None</code>\n"
    
    status_text += "\n<i>BOT BY - kคli liຖนxx</i>"
    
    await safe_send(
        update.message.reply_text,
        status_text,
        parse_mode=constants.ParseMode.HTML,
        reply_markup=reply_markup
    )

@authorized_command_handler
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels an ongoing scan."""
    chat_id = update.effective_chat.id
    task = ongoing_searches.get(chat_id)
    
    if task and not task.done():
        task.cancel()
        ongoing_searches.pop(chat_id, None)
        checked_nid_counts.pop(chat_id, None)
        total_nids_to_check.pop(chat_id, None)
        user_progress_messages.pop(chat_id, None)
        
        await safe_send(
            update.message.reply_text,
            "🛑 <b>Scan Cancelled</b>\n\n"
            "The scan has been successfully stopped.\n"
            "You can start a new scan anytime.\n\n"
            "<i>BOT BY - kคli liຖนxx</i>",
            parse_mode=constants.ParseMode.HTML
        )
    else:
        await safe_send(
            update.message.reply_text,
            "ℹ️ <b>No Active Scan</b>\n\n"
            "There is no scan currently running.\n"
            "Use /search to start a new scan.\n\n"
            "<i>BOT BY - kคli liຖนxx</i>",
            parse_mode=constants.ParseMode.HTML
        )

@authorized_command_handler
async def authorize_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Authorizes a user to use the bot."""
    if len(context.args) < 1:
        await safe_send(
            update.message.reply_text,
            "❗ <b>Usage:</b> <code>/au &lt;user_id&gt;</code>\n\n"
            "Example: <code>/au 123456789</code>\n\n"
            "<i>BOT BY - kคli liຖนxx</i>",
            parse_mode=constants.ParseMode.HTML
        )
        return
    
    try:
        user_id = int(context.args[0])
        authorized_users.add(user_id)
        await safe_send(
            update.message.reply_text,
            f"✅ <b>User Authorized</b>\n\n"
            f"User <code>{user_id}</code> has been added to the authorized list.\n\n"
            f"<i>BOT BY - kคli liຖนxx</i>",
            parse_mode=constants.ParseMode.HTML
        )
        logger.info(f"User {user_id} authorized by owner {update.effective_user.id}")
    except ValueError:
        await safe_send(
            update.message.reply_text,
            "❗ <b>Invalid User ID</b>\n\n"
            "Please provide a valid numeric user ID.\n\n"
            "<i>BOT BY - kคli liຖนxx</i>",
            parse_mode=constants.ParseMode.HTML
        )

@authorized_command_handler
async def revoke_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Revokes authorization for a user."""
    if len(context.args) < 1:
        await safe_send(
            update.message.reply_text,
            "❗ <b>Usage:</b> <code>/ru &lt;user_id&gt;</code>\n\n"
            "Example: <code>/ru 123456789</code>\n\n"
            "<i>BOT BY - kคli liຖนxx</i>",
            parse_mode=constants.ParseMode.HTML
        )
        return
    
    try:
        user_id = int(context.args[0])
        if user_id in authorized_users:
            authorized_users.remove(user_id)
            await safe_send(
                update.message.reply_text,
                f"🚫 <b>Authorization Revoked</b>\n\n"
                f"User <code>{user_id}</code> has been removed from the authorized list.\n\n"
                f"<i>BOT BY - kคli liຖนxx</i>",
                parse_mode=constants.ParseMode.HTML
            )
            logger.info(f"User {user_id} authorization revoked by owner {update.effective_user.id}")
        else:
            await safe_send(
                update.message.reply_text,
                f"⚠️ <b>User Not Found</b>\n\n"
                f"User <code>{user_id}</code> was not in the authorized list.\n\n"
                f"<i>BOT BY - kคli liຖนxx</i>",
                parse_mode=constants.ParseMode.HTML
            )
    except ValueError:
        await safe_send(
            update.message.reply_text,
            "❗ <b>Invalid User ID</b>\n\n"
            "Please provide a valid numeric user ID.\n\n"
            "<i>BOT BY - kคli liຖนxx</i>",
            parse_mode=constants.ParseMode.HTML
        )

@authorized_command_handler
async def list_authorized(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists all authorized users."""
    keyboard = [[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if not authorized_users:
        await safe_send(
            update.message.reply_text,
            "📋 <b>Authorized Users</b>\n\n"
            "👑 <b>Owner:</b> <code>{}</code>\n"
            "👥 <b>Additional Users:</b> <code>None</code>\n\n"
            "<i>BOT BY - kคli liຖนxx</i>".format(OWNER_ID),
            parse_mode=constants.ParseMode.HTML,
            reply_markup=reply_markup
        )
        return
    
    user_list = "\n".join([f"• <code>{user_id}</code>" for user_id in sorted(authorized_users)])
    await safe_send(
        update.message.reply_text,
        f"📋 <b>Authorized Users</b>\n\n"
        f"👑 <b>Owner:</b> <code>{OWNER_ID}</code>\n"
        f"👥 <b>Additional Users:</b>\n{user_list}\n\n"
        f"<i>BOT BY - kคli liຖนxx</i>",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=reply_markup
    )

@authorized_command_handler
async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts scanning NIDs within a specified range."""
    chat_id = update.effective_chat.id
    args = context.args
    
    if len(args) < 2:
        await safe_send(
            update.message.reply_text,
            "❗ <b>Usage:</b> <code>/search &lt;start_nid&gt; &lt;end_nid&gt; [batch_size]</code>\n\n"
            "<b>Examples:</b>\n"
            "• <code>/search 1000 2000</code>\n"
            "• <code>/search 1000 2000 500</code>\n\n"
            "<b>Parameters:</b>\n"
            "• start_nid: Starting NID number\n"
            "• end_nid: Ending NID number\n"
            "• batch_size: Optional (default: 1000)\n\n"
            "<i>BOT BY - kคli liຖนxx</i>",
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
                "⚠️ <b>Invalid NID Values</b>\n\n"
                "NID values must be positive integers.\n\n"
                "<i>BOT BY - kคli liຖนxx</i>",
                parse_mode=constants.ParseMode.HTML
            )
            return
        
        if start_nid > end_nid:
            await safe_send(
                update.message.reply_text,
                "⚠️ <b>Invalid Range</b>\n\n"
                "Start NID must be less than or equal to End NID.\n\n"
                "<i>BOT BY - kคli liຖนxx</i>",
                parse_mode=constants.ParseMode.HTML
            )
            return
        
        if chat_id in ongoing_searches and not ongoing_searches[chat_id].done():
            await safe_send(
                update.message.reply_text,
                "⏳ <b>Scan Already Running</b>\n\n"
                "A scan is already in progress. Use /cancel to stop it first.\n\n"
                "<i>BOT BY - kคli liຖนxx</i>",
                parse_mode=constants.ParseMode.HTML
            )
            return
        
        # Initialize user-specific data
        checked_nid_counts[chat_id] = 0
        total_nids_to_check[chat_id] = end_nid - start_nid + 1
        
        # Create and start the search task
        task = asyncio.create_task(
            perform_search(chat_id, start_nid, end_nid, batch_size, context)
        )
        ongoing_searches[chat_id] = task
        
    except ValueError:
        await safe_send(
            update.message.reply_text,
            "❗ <b>Invalid Input</b>\n\n"
            "Please provide valid integer values for NID range and batch size.\n\n"
            "<i>BOT BY - kคli liຖนxx</i>",
            parse_mode=constants.ParseMode.HTML
        )

# === SEARCH TASK ===
async def perform_search(chat_id, start_nid, end_nid, batch_size, context):
    """Performs the asynchronous NID scanning in the background."""
    total = end_nid - start_nid + 1
    
    await safe_send(
        context.bot.send_chat_action,
        chat_id=chat_id,
        action=constants.ChatAction.TYPING
    )
    
    keyboard = [
        [InlineKeyboardButton("📊 View Progress", callback_data=f"progress_{chat_id}")],
        [InlineKeyboardButton("🛑 Cancel Scan", callback_data=f"cancel_{chat_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    intro_msg = await safe_send(
        context.bot.send_message,
        chat_id=chat_id,
        text=f"🔍 <b>Scan Initiated</b>\n\n"
             f"📚 <b>Range:</b> {start_nid} - {end_nid}\n"
             f"📊 <b>Total NIDs:</b> {total}\n"
             f"📦 <b>Batch Size:</b> {batch_size}\n"
             f"⚡ <b>Concurrency:</b> {MAX_CONCURRENT_REQUESTS}\n\n"
             f"⏳ <b>Status:</b> <code>Initializing...</code>\n\n"
             f"<i>BOT BY - kคli liຖนxx</i>",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=reply_markup
    )
    
    if intro_msg:
        user_progress_messages[chat_id] = intro_msg.message_id
    
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    found_count = 0
    
    try:
        async with aiohttp.ClientSession() as session:
            for i in range(start_nid, end_nid + 1, batch_size):
                if chat_id not in ongoing_searches or ongoing_searches[chat_id].cancelled():
                    await safe_send(
                        context.bot.send_message,
                        chat_id=chat_id,
                        text="🛑 <b>Scan Cancelled</b>\n\n"
                             "The scan was cancelled by user request.\n\n"
                             "<i>BOT BY - kคli liຖนxx</i>",
                        parse_mode=constants.ParseMode.HTML
                    )
                    return
                
                batch = range(i, min(i + batch_size, end_nid + 1))
                
                results = await asyncio.gather(
                    *(fetch_test_data(session, nid, semaphore) for nid in batch),
                    return_exceptions=True
                )
                
                batch_found = []
                for result in results:
                    if isinstance(result, Exception):
                        logger.error(f"Error in batch processing: {result}")
                        continue
                    
                    result_nid, result_title = result
                    checked_nid_counts[chat_id] += 1
                    
                    if result_title:
                        batch_found.append((result_nid, result_title))
                
                if batch_found:
                    found_count += len(batch_found)
                    for nid, title in batch_found:
                        item_msg = (
                            f"🎯 <b>NID Found!</b>\n\n"
                            f"📝 <b>Title:</b> {title}\n"
                            f"🆔 <b>NID:</b> <code>{nid}</code>\n\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n\n"
                            f"<i>BOT BY - kคli liຖนxx</i>"
                        )
                        await safe_send(
                            context.bot.send_message,
                            chat_id=chat_id,
                            text=item_msg,
                            parse_mode=constants.ParseMode.HTML
                        )
                
                if checked_nid_counts[chat_id] % 100 == 0 or checked_nid_counts[chat_id] == total:
                    progress_percent = round((checked_nid_counts[chat_id] / total) * 100, 1)
                    progress_bar = "█" * int(progress_percent / 5) + "░" * (20 - int(progress_percent / 5))
                    
                    keyboard = [
                        [InlineKeyboardButton(f"📊 Progress: {progress_percent}%", callback_data=f"progress_{chat_id}")],
                        [InlineKeyboardButton("🛑 Cancel Scan", callback_data=f"cancel_{chat_id}")]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    try:
                        if chat_id in user_progress_messages:
                            await safe_send(
                                context.bot.edit_message_text,
                                chat_id=chat_id,
                                message_id=user_progress_messages[chat_id],
                                text=f"🔍 <b>Scan In Progress</b>\n\n"
                                     f"📚 <b>Range:</b> {start_nid} - {end_nid}\n"
                                     f"📊 <b>Progress:</b> {progress_bar} {progress_percent}%\n"
                                     f"📈 <b>Checked:</b> {checked_nid_counts[chat_id]} / {total}\n"
                                     f"🎯 <b>Found:</b> {found_count}\n"
                                     f"📦 <b>Batch Size:</b> {batch_size}\n\n"
                                     f"⏳ <b>Status:</b> <code>Scanning...</code>\n\n"
                                     f"<i>BOT BY - kคli liຖนxx</i>",
                                parse_mode=constants.ParseMode.HTML,
                                reply_markup=reply_markup
                            )
                    except Exception as e:
                        logger.warning(f"Could not edit progress message: {e}")
                
                await asyncio.sleep(0.1)
                
    except asyncio.CancelledError:
        await safe_send(
            context.bot.send_message,
            chat_id=chat_id,
            text="🛑 <b>Scan Cancelled</b>\n\n"
                 "The scan was successfully cancelled.\n\n"
                 "<i>BOT BY - kคli liຖนxx</i>",
            parse_mode=constants.ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error during scan for chat {chat_id}: {e}", exc_info=True)
        await safe_send(
            context.bot.send_message,
            chat_id=chat_id,
            text=f"❌ <b>Scan Error</b>\n\n"
                 f"An error occurred: {str(e)}\n\n"
                 f"<i>BOT BY - kคli liຖนxx</i>",
            parse_mode=constants.ParseMode.HTML
        )
    finally:
        ongoing_searches.pop(chat_id, None)
        checked_nid_counts.pop(chat_id, None)
        total_nids_to_check.pop(chat_id, None)
        user_progress_messages.pop(chat_id, None)
        
        final_count = checked_nid_counts.get(chat_id, total)
        await safe_send(
            context.bot.send_message,
            chat_id=chat_id,
            text=f"✅ <b>Scan Complete!</b>\n\n"
                 f"📊 <b>Summary:</b>\n"
                 f"📚 <b>Range:</b> {start_nid} - {end_nid}\n"
                 f"📈 <b>Checked:</b> {final_count} NIDs\n"
                 f"🎯 <b>Found:</b> {found_count} results\n"
                 f"🏁 <b>Status:</b> <code>Completed</code>\n\n"
                 f"Thank you for using NID Scanner Bot!\n\n"
                 f"<i>BOT BY - kคli liຖนxx</i>",
            parse_mode=constants.ParseMode.HTML
        )

# === CALLBACK HANDLERS ===
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles button callbacks from inline keyboards."""
    query = update.callback_query
    await query.answer()
    
    chat_id = update.effective_chat.id
    data = query.data
    
    if not is_authorized(update.effective_user.id):
        await query.edit_message_text(
            "🚫 <b>Access Denied</b>\n\n"
            "You are not authorized to use this bot.\n"
            "Please contact the bot owner for access.\n\n"
            "<i>BOT BY - kคli liຖนxx</i>",
            parse_mode=constants.ParseMode.HTML
        )
        return
    
    if data == "start":
        await start(update, context)
    elif data == "help":
        await help_command(update, context)
    elif data == "status":
        await status(update, context)
    elif data == "admin":
        await admin_commands(update, context)
    elif data == "list_users":
        await list_authorized(update, context)
    elif data == "listall":
        await listall_command(update, context)
    elif data.startswith("progress_"):
        current = checked_nid_counts.get(chat_id, 0)
        total = total_nids_to_check.get(chat_id, '?')
        progress_percent = round((current / total) * 100, 1) if total != '?' else 0
        progress_bar = "█" * int(progress_percent / 5) + "░" * (20 - int(progress_percent / 5))
        
        await safe_send(
            context.bot.send_message,
            chat_id=chat_id,
            text=f"📊 <b>Current Progress</b>\n\n"
                 f"{progress_bar} {progress_percent}%\n"
                 f"📈 <b>Checked:</b> {current} / {total}\n\n"
                 f"<i>BOT BY - kคli liຖนxx</i>",
            parse_mode=constants.ParseMode.HTML
        )
    elif data.startswith("cancel_"):
        task = ongoing_searches.get(chat_id)
        if task and not task.done():
            task.cancel()
            await safe_send(
                context.bot.send_message,
                chat_id=chat_id,
                text="🛑 <b>Cancelling Scan...</b>\n\n"
                     "Please wait while the scan stops.\n\n"
                     "<i>BOT BY - kคli liຖนxx</i>",
                parse_mode=constants.ParseMode.HTML
            )
        else:
            await safe_send(
                context.bot.send_message,
                chat_id=chat_id,
                text="ℹ️ <b>No Active Scan</b>\n\n"
                     "There is no scan currently running.\n\n"
                     "<i>BOT BY - kคli liຖนxx</i>",
                parse_mode=constants.ParseMode.HTML
            )
    elif data == "search_prompt":
        await safe_send(
            context.bot.send_message,
            chat_id=chat_id,
            text="🔍 <b>Start a New Scan</b>\n\n"
                 "Use the command:\n"
                 "<code>/search &lt;start_nid&gt; &lt;end_nid&gt; [batch_size]</code>\n\n"
                 "<b>Example:</b>\n"
                 "<code>/search 1000 2000 500</code>\n\n"
                 "<i>BOT BY - kคli liຖนxx</i>",
            parse_mode=constants.ParseMode.HTML
        )
    elif data == "authorize_prompt":
        await safe_send(
            context.bot.send_message,
            chat_id=chat_id,
            text="➕ <b>Authorize User</b>\n\n"
                 "Use the command:\n"
                 "<code>/au &lt;user_id&gt;</code>\n\n"
                 "<b>Example:</b>\n"
                 "<code>/au 123456789</code>\n\n"
                 "<i>BOT BY - kคli liຖนxx</i>",
            parse_mode=constants.ParseMode.HTML
        )
    elif data == "revoke_prompt":
        await safe_send(
            context.bot.send_message,
            chat_id=chat_id,
            text="➖ <b>Revoke User</b>\n\n"
                 "Use the command:\n"
                 "<code>/ru &lt;user_id&gt;</code>\n\n"
                 "<b>Example:</b>\n"
                 "<code>/ru 123456789</code>\n\n"
                 "<i>BOT BY - kคli liຖนxx</i>",
            parse_mode=constants.ParseMode.HTML
        )
    elif data == "bot_stats":
        await safe_send(
            context.bot.send_message,
            chat_id=chat_id,
            text=f"📊 <b>Bot Statistics</b>\n\n"
                 f"👑 <b>Owner:</b> <code>{OWNER_ID}</code>\n"
                 f"👥 <b>Authorized Users:</b> {len(authorized_users)}\n"
                 f"🔍 <b>Active Scans:</b> {len(ongoing_searches)}\n"
                 f"⚡ <b>Max Concurrent:</b> {MAX_CONCURRENT_REQUESTS}\n"
                 f"📦 <b>Default Batch:</b> {DEFAULT_BATCH_SIZE}\n\n"
                 f"<i>BOT BY - kคli liຖนxx</i>",
            parse_mode=constants.ParseMode.HTML
        )

async def unauthorized_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for unauthorized users trying to use commands."""
    await safe_send(
        update.message.reply_text,
        "🚫 <b>Access Denied</b>\n\n"
        "You are not authorized to use this bot.\n"
        "Please contact the bot owner for access.\n\n"
        "<i>BOT BY - kคli liຖนxx</i>",
        parse_mode=constants.ParseMode.HTML
    )

# === MAIN ===
def main():
    """Starts the bot."""
    if not TOKEN:
        logger.error("🚫 BOT_TOKEN environment variable not set.")
        return
    
    app = Application.builder().token(TOKEN).concurrent_updates(True).build()
    
    # Command handlers with authorization check
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("listall", listall_command))
    app.add_handler(CommandHandler("admin", admin_commands))
    app.add_handler(CommandHandler("search", search))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("au", authorize_user))
    app.add_handler(CommandHandler("ru", revoke_user))
    app.add_handler(CommandHandler("list", list_authorized))
    
    # Callback handler
    app.add_handler(CallbackQueryHandler(button_callback))
    
    logger.info(f"🚀 Bot started for Owner ID: {OWNER_ID}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
